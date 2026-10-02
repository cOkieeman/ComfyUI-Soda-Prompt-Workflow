import json
import sys
import unittest
import tempfile
import io
from pathlib import Path
from PIL import Image
from unittest.mock import AsyncMock, patch
import test_workflow

u = sys.modules['soda_test.unified']

class UnifiedTests(unittest.IsolatedAsyncioTestCase):
    def test_local_thumbnail_uses_source_path_resolution_and_bounded_dimensions(self):
        material = sys.modules['soda_test.material_nodes']
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'中文 picture.png'
            Image.new('RGB', (640, 400), (20, 90, 160)).save(path)
            for value in (str(path), '"'+str(path)+'"'):
                image = Image.open(io.BytesIO(material.local_thumbnail(value)))
                self.assertEqual(image.size, (320, 200))
                self.assertEqual(image.format, 'JPEG')
            with patch.object(u.folder_paths, 'get_annotated_filepath', return_value=str(path), create=True) as resolve:
                material.local_thumbnail('uploaded/picture.png')
                resolve.assert_called_once()
            for value in ('', str(Path(directory)/'missing.png')):
                with self.assertRaises((ValueError, OSError)): material.local_thumbnail(value)

    def test_only_gallery_requests_gallery(self):
        node=u.SodaUnifiedSource()
        for source in (u.LOCAL,u.DFLOW,u.TEXT):
            self.assertEqual(node.check_lazy_status(source),[])
        self.assertEqual(node.check_lazy_status(u.GALLERY),['gallery_image','gallery_text'])
        self.assertFalse(u.SodaGallerySource.OUTPUT_NODE)

    async def test_text_does_not_touch_dflow_or_file(self):
        with patch.object(u.SodaDFlowSource,'run',new_callable=AsyncMock) as remote, patch.object(u.Image,'open') as disk:
            result=await u.SodaUnifiedSource().run(u.TEXT,'missing.png','kept',4173,'invalid')
            self.assertEqual(result[1],'kept')
            remote.assert_not_awaited(); disk.assert_not_called()

    async def test_wrong_source_writeback_skips(self):
        result=await u.SodaUnifiedWriteback().run('text','{"source":"text"}',True)
        self.assertIn('跳过',result['result'][0])

    async def test_creation_preserves_variants_and_selection(self):
        with patch.object(u.SodaTextSuite,'run',AsyncMock(return_value=('all', '{}'))), patch.object(u.SodaVariant,'run',return_value=('chosen','{}')) as select:
            result=await u.SodaUnifiedProcess().run(u.CREATE,u.suite.PIXEL,'',True,0,180,variant_index=1,source_text='request')
            self.assertEqual(result[0],'chosen'); self.assertEqual(result[2],'all')
            select.assert_called_once_with('{}',1)

    async def test_original_mode_zero_paid_calls(self):
        result=await u.SodaUnifiedProcess().run('使用素材原提示词',u.suite.PIXEL,'',True,0,180,source_text='kept')
        self.assertEqual(result[0],'kept'); self.assertEqual(json.loads(result[1])['api_calls'],0)

if __name__=='__main__': unittest.main()
