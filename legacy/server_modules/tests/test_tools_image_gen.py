import base64
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from server_modules import tools_image_gen


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO7Z0TsAAAAASUVORK5CYII="
)


class _FakeImagesApi:
    def __init__(self) -> None:
        self.last_kwargs = None

    def generate(self, **kwargs):
        self.last_kwargs = kwargs
        return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(PNG_BYTES).decode("ascii"))])


class _FakeOpenAIClient:
    def __init__(self) -> None:
        self.images = _FakeImagesApi()


class ImageGenerationToolTests(unittest.TestCase):
    def test_dalle_called_with_correct_params(self):
        client = _FakeOpenAIClient()
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "poster.png"
            result = tools_image_gen.generate_image(
                prompt="A red fox in a studio portrait",
                model="dall-e-3",
                size="512x512",
                quality="hd",
                n=1,
                save_to=str(output_path),
                client=client,
            )

            self.assertEqual(result, [str(output_path)])
            self.assertTrue(output_path.exists())

        self.assertEqual(client.images.last_kwargs["model"], "dall-e-3")
        self.assertEqual(client.images.last_kwargs["prompt"], "A red fox in a studio portrait")
        self.assertEqual(client.images.last_kwargs["size"], "512x512")
        self.assertEqual(client.images.last_kwargs["quality"], "hd")
        self.assertEqual(client.images.last_kwargs["n"], 1)

    def test_image_saved_to_default_path(self):
        client = _FakeOpenAIClient()
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                with patch("server_modules.tools_image_gen._openai_client", return_value=client):
                    result = tools_image_gen.generate_image(prompt="A blue cat on a chair")
            finally:
                os.chdir(cwd)

            self.assertEqual(len(result), 1)
            saved_path = Path(result[0])
            self.assertTrue(saved_path.exists())
            self.assertIn(str(Path(tmpdir) / ".orion-stack" / "generated_images"), str(saved_path))


if __name__ == "__main__":
    unittest.main()
