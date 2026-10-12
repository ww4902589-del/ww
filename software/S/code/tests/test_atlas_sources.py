import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent/'tools'))
import atlas_sources as S
import style_atlas as A

class SourceTests(unittest.TestCase):
    def test_persistent_roots_and_duplicate_normalization(self):
        with tempfile.TemporaryDirectory() as temp:
            result=S.save(temp,[temp,temp])
            self.assertEqual(len(result['roots']),1)
            self.assertEqual(S.load(temp),result)
            with self.assertRaises(ValueError):S.save(temp,[os.path.join(temp,'missing')])
            self.assertEqual(S.load(temp),result)

    def test_direct_scan_without_reviews_and_overlapping_roots(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);nested=root/'new';nested.mkdir()
            from PIL import Image
            Image.new('RGB',(8,8)).save(nested/'work.png')
            (nested/'fake.png').write_bytes(b'not an image')
            (nested/'art.tiff').write_bytes(b'unsupported')
            skipped=root/'_build_test';skipped.mkdir()
            Image.new('RGB',(8,8)).save(skipped/'cache.png')
            paths,reports=S.scan([temp,str(nested)],[],A._skip_dir,A.IMG_EXT,lambda p:A.is_sane_image(p,{}))
            self.assertEqual(paths,[str(nested/'work.png').replace('\\','/')])
            self.assertEqual(reports[0]['invalid'],1)
            self.assertEqual(reports[0]['unsupported'],1)
            self.assertEqual(reports[0]['excluded_directories'],1)
            self.assertEqual(reports[1]['already_indexed'],1)

    def test_missing_root_is_visible_and_records_not_duplicated(self):
        with tempfile.TemporaryDirectory() as temp:
            from PIL import Image
            path=Path(temp)/'existing.png';Image.new('RGB',(8,8)).save(path)
            found,reports=S.scan([temp,str(Path(temp)/'unmounted')],[str(path)],A._skip_dir,A.IMG_EXT,lambda p:True)
            self.assertEqual(found,[])
            self.assertEqual(reports[0]['already_indexed'],1)
            self.assertFalse(reports[1]['exists'])

if __name__=='__main__':unittest.main()
