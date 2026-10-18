import unittest
import tempfile
import json
from xml.etree import ElementTree as ET
from pathlib import Path
from src.app_identity import municipality_photo, brand_symbol

class IdentityTests(unittest.TestCase):
    def test_brand_has_seven_stars(self):
        import base64
        from io import BytesIO
        from PIL import Image
        # st.html accepts HTML only: inline SVG would disappear in the browser.
        symbol=ET.fromstring(brand_symbol())
        image=symbol.find('img')
        self.assertIsNotNone(image)
        self.assertIsNone(symbol.find('{http://www.w3.org/2000/svg}svg'))
        self.assertTrue(image.attrib['src'].startswith('data:image/png;base64,'))
        png=Image.open(BytesIO(base64.b64decode(image.attrib['src'].split(',',1)[1])))
        self.assertEqual(png.size,(232,192))
        # Count seven distinct white shapes in the actual image sent to the UI.
        for size in [(58,48),(45,43),(40,36),(36,34)]:
            with self.subTest(size=size):
                scaled=png.copy();scaled.thumbnail(size,Image.Resampling.LANCZOS)
                scaled=scaled.convert('RGB')
                white={(x,y) for y in range(scaled.height) for x in range(scaled.width)
                       if min(scaled.getpixel((x,y)))>200}
                stars=0
                while white:
                    stars+=1;pending=[white.pop()]
                    while pending:
                        x,y=pending.pop()
                        for dx in (-1,0,1):
                            for dy in (-1,0,1):
                                point=(x+dx,y+dy)
                                if point in white:white.remove(point);pending.append(point)
                self.assertEqual(stars,7)

    def test_missing_photo_names_municipality_and_escapes_html(self):
        with tempfile.TemporaryDirectory() as root:
            html = municipality_photo(root, '28120', 'Rascafría <prueba>')
            self.assertIn('Sin fotografía verificada', html)
            self.assertIn('&lt;prueba&gt;', html)
            self.assertNotIn('<img', html)

    def test_local_image_and_attribution_without_remote_image_fetch(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'data/reference/municipality_photos';path.mkdir(parents=True)
            (path/'28120.webp').write_bytes(b'test')
            (path/'catalog.json').write_text(json.dumps({'28120':{'status':'available','file':'28120.webp','author':'A & B','license':'CC BY-SA 4.0','source_url':'https://commons.wikimedia.org/wiki/File:Example.jpg'}}))
            html=municipality_photo(root,'28120','Rascafría')
            self.assertIn('data:image/webp;base64,',html)
            self.assertIn('A &amp; B',html)
            self.assertIn('CC BY-SA 4.0',html)
            self.assertNotIn('src="https:',html)
