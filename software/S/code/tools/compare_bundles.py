"""Compare two PyInstaller bundles entry by entry, so a size gap is explained.

The baseline ``S.exe`` is 168.9 MB and the rebuild is 27.3 MB, while the two spec
files differ by about 30 KB of ``datas``. This reads both embedded archives and
reports what is only in one, what is only in the other, and where a shared entry
grew -- which is the only way to attribute the difference to a cause.
"""

from __future__ import annotations

import pathlib
import sys

from PyInstaller.archive.readers import CArchiveReader


def toc(path: pathlib.Path) -> dict[str, tuple[int, int]]:
    """name -> (uncompressed size, compressed size) for one bundle."""
    reader = CArchiveReader(str(path))
    return {name: (entry[2], entry[1]) for name, entry in reader.toc.items()}


def megabytes(value: int) -> str:
    return f"{value / 1048576:.2f} MB"


def main() -> int:
    old_path, new_path = (pathlib.Path(arg) for arg in sys.argv[1:3])
    old, new = toc(old_path), toc(new_path)

    print(f"基线 {old_path.name}: {old_path.stat().st_size / 1048576:.1f} MB 归档，{len(old)} 项")
    print(f"新版 {new_path.name}: {new_path.stat().st_size / 1048576:.1f} MB 归档，{len(new)} 项\n")

    only_old = sorted(((v[0], k) for k, v in old.items() if k not in new), reverse=True)
    only_new = sorted(((v[0], k) for k, v in new.items() if k not in old), reverse=True)

    total_only_old = sum(size for size, _ in only_old)
    total_only_new = sum(size for size, _ in only_new)
    print(f"只在基线里有：{len(only_old)} 项，解压合计 {megabytes(total_only_old)}")
    for size, name in only_old[:20]:
        print(f"   {megabytes(size):>10}  {name}")
    if len(only_old) > 20:
        # Group what remains by top-level folder so the tail is still readable.
        tail = only_old[20:]
        print(f"   ...另有 {len(tail)} 项合计 {megabytes(sum(s for s, _ in tail))}")

    print(f"\n只在新版里有：{len(only_new)} 项，解压合计 {megabytes(total_only_new)}")
    for size, name in only_new[:12]:
        print(f"   {megabytes(size):>10}  {name}")

    # Shared entries whose size moved a lot.
    print("\n共有但体积变化明显（>0.5 MB）：")
    shared = [
        (new[k][0] - old[k][0], old[k][0], new[k][0], k)
        for k in old.keys() & new.keys()
    ]
    for delta, before, after, name in sorted(shared, reverse=True):
        if abs(delta) > 512 * 1024:
            print(f"   {megabytes(before):>10} -> {megabytes(after):>10}  {name}")

    # Careful: the loop variable of the printed passes above must not leak into
    # these sums -- binding the wrong name here silently produced "MKL 9.77 MB"
    # for a bundle whose MKL entries are visibly hundreds of megabytes.
    mkl = sum(old[name][0] for name in old if name.lower().startswith("mkl"))
    numpy_old = sum(old[name][0] for name in old if "numpy" in name.lower())
    numpy_new = sum(new[name][0] for name in new if "numpy" in name.lower())
    print("\n结论数据：")
    print(f"  基线里的 MKL 系列 DLL      {megabytes(mkl)}")
    print(f"  基线里的 numpy 相关        {megabytes(numpy_old)}")
    print(f"  新版里的 numpy 相关        {megabytes(numpy_new)}")
    print(f"  两者归档体积差             {megabytes(old_path.stat().st_size - new_path.stat().st_size)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
