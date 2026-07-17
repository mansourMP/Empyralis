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

    def test_per_call_api_key_is_threaded_to_the_openai_client_not_the_env_var(self):
        """agent_capability_service resolves a per-agent (BYOK or platform)
        key and passes it explicitly — this must be what actually reaches
        the OpenAI client, not whatever OPENAI_API_KEY happens to be set to
        in the process environment."""
        captured_keys = []

        def _fake_openai_client(*, api_key=None):
            captured_keys.append(api_key)
            return _FakeOpenAIClient()

        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch.dict(os.environ, {"OPENAI_API_KEY": "sk-WRONG-process-env-key"}, clear=False),
                patch("server_modules.tools_image_gen._openai_client", side_effect=_fake_openai_client),
                # Unrelated to what's under test here: the Rust runtime
                # kernel governance gate on the FILE WRITE (unbuilt in this
                # environment — see this file's own pre-existing
                # test_dalle_called_with_correct_params /
                # test_image_saved_to_default_path, which fail identically
                # and unconditionally on a clean checkout with no capability
                # changes at all).
                patch("server_modules.tools_image_gen._enforce_generated_image_file_write"),
            ):
                tools_image_gen.generate_image(
                    prompt="A resolved-per-agent key test",
                    save_to=str(Path(tmpdir) / "out.png"),
                    api_key="sk-resolved-per-agent-key",
                )
        self.assertEqual(captured_keys, ["sk-resolved-per-agent-key"])

    def test_no_api_key_argument_falls_back_to_process_env_unchanged(self):
        """Exact-behavior-preserving for every caller that hasn't been
        threaded through the capability resolver yet."""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-env-fallback"}, clear=False):
            client = tools_image_gen._openai_client()
        self.assertEqual(client.api_key, "sk-env-fallback")

    def test_stability_api_key_argument_overrides_env_var(self):
        captured_headers = {}

        def _fake_post(self_client, url, **kwargs):
            captured_headers.update(kwargs.get("headers") or {})
            return SimpleNamespace(status_code=200, content=PNG_BYTES, raise_for_status=lambda: None)

        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch.dict(os.environ, {"STABILITY_API_KEY": "sk-WRONG-env-key"}, clear=False),
                patch("httpx.Client.post", new=_fake_post),
                patch("server_modules.tools_image_gen._enforce_generated_image_file_write"),
            ):
                result = tools_image_gen.generate_image(
                    prompt="stability override test",
                    model="stable-diffusion",
                    save_to=str(Path(tmpdir) / "out.png"),
                    api_key="sk-resolved-stability-key",
                )
            self.assertEqual(len(result), 1)
            self.assertTrue(Path(result[0]).exists())
        self.assertEqual(captured_headers.get("Authorization"), "Bearer sk-resolved-stability-key")


if __name__ == "__main__":
    unittest.main()
