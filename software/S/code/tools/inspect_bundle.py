"""List what is actually inside a PyInstaller one-file exe, biggest first.

The question is why the baseline ``S.exe`` is 177 MB and the rebuild is 28 MB when
their spec files differ only in ``datas`` (about 30 KB). The archive table of
contents answers it directly, so this reads the embedded CArchive rather than
guessing from the spec.
"""

from __future__ import annotations

import pathlib
import sys
from collections import defaultdict

from PyInstaller.archive.readers import CArchiveReader

#: Entry type codes PyInstaller writes into the TOC.
TYPE_NAMES = {"b": "binary/DLL", "z": "python-module", "m": "data", "x": "data", "s": "script"}


def describe(path: pathlib.Path) -> None:
    size = path.stat().st_size
    print(f"\n{'=' * 72}\n{path}  ({size:,} bytes = {size / 1048576:.1f} MB)\n{'=' * 72}")
    reader = CArchiveReader(str(path))
    entries = []
    for name, entry in reader.toc.items():
        # (dpos, dlen, ulen, flag, typcd)
        dpos, dlen, ulen, _flag, typcd = entry
        entries.append((ulen, dlen, typcd, name))
    entries.sort(reverse=True)

    total_ul = sum(e[0] for e in entries)
    total_c = sum(e[1] for e in entries)
    print(f"条目数 {len(entries)}  解压后合计 {total_ul / 1048576:.1f} MB  归档内压缩后 {total_c / 1048576:.1f} MB")

    by_type = defaultdict(lambda: [0, 0])
    for ulen, dlen, typcd, _name in entries:
        bucket = by_type[TYPE_NAMES.get(typcd, typcd)]
        bucket[0] += 1
        bucket[1] += ulen
    for kind, (count, ul) in sorted(by_type.items(), key=lambda kv: -kv[1][1]):
        print(f"  {kind:<14} {count:>6} 个   {ul / 1048576:>8.1f} MB")

    print("\n最大的 25 个条目：")
    for ulen, dlen, typcd, name in entries[:25]:
        print(f"  {ulen / 1048576:>8.2f} MB (压缩 {dlen / 1048576:>7.2f}) [{TYPE_NAMES.get(typcd, typcd):<13}] {name[:78]}")

    top = entries[:60]
    print(f"\n最大的 60 项合计 {sum(e[0] for e in top) / 1048576:.1f} MB"
          f"（占解压总量 {100 * sum(e[0] for e in top) / total_ul:.0f}%）")


for argument in sys.argv[1:]:
    describe(pathlib.Path(argument))
