import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import create_app
from app.settings import PROJECT_ROOT, Settings


class StartupTests(unittest.TestCase):
    def test_startup_health_database_and_private_files(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(_env_file=None, data_dir=Path(directory) / "runtime")
            application = create_app(settings)
            with TestClient(application) as client:
                response = client.get("/api/v1/health")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {"status": "ok", "apiVersion": "1.1.0"})
                self.assertEqual(response.headers["cache-control"], "no-store")
                self.assertTrue(response.headers["x-request-id"].startswith("req-"))
                self.assertTrue(settings.database_path.is_file())
                self.assertTrue(settings.artifacts_dir.is_dir())
                with application.state.engine.connect() as connection:
                    self.assertEqual(connection.scalar(text("PRAGMA foreign_keys")), 1)
                response = client.get("/data/codepoet.sqlite3")
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json()["error"]["code"], "RESOURCE_NOT_FOUND")

    def test_relative_data_directory_is_rooted_at_project(self):
        settings = Settings(_env_file=None, data_dir=Path("data"))
        self.assertEqual(settings.data_dir, PROJECT_ROOT / "data")


if __name__ == "__main__":
    unittest.main()
