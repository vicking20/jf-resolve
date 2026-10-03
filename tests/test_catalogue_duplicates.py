import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from experimental.backend.services import catalogue_service


class CatalogueSyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_syncs_run_one_at_a_time(self):
        active = 0
        overlap = []

        async def fake_sync(self, cat_def, tmdb):
            nonlocal active
            active += 1
            overlap.append(active)
            await asyncio.sleep(0.02)
            active -= 1

        service = catalogue_service.CatalogueService.__new__(catalogue_service.CatalogueService)
        with patch.object(catalogue_service.CatalogueService, "_sync_catalogue_items", fake_sync):
            await asyncio.gather(*(service.sync_catalogue_items({}, None) for _ in range(3)))
        self.assertEqual(max(overlap), 1)


if __name__ == "__main__":
    unittest.main()
