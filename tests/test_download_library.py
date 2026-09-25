import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from experimental.backend.core.database import Base
from experimental.backend.models.media import MediaSourceModel
from experimental.backend.models.media_item import MediaItem, MediaType
from experimental.backend.models.library_path import LibraryPath
from experimental.backend.services.download_library_service import download_target, register_local_file, read_identity, write_identity
from experimental.backend.services.download_service import DownloadService
from experimental.backend.services.library.active_scanner import ActiveScanner
from experimental.backend.services.source_orchestrator import source_orchestrator


class DownloadLibraryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.temp = tempfile.TemporaryDirectory()
        self.file = Path(self.temp.name) / 'actual.mkv'
        self.file.write_bytes(b'original file')
        self.movie_uuid = str(uuid.uuid4())
        self.tv_uuid = str(uuid.uuid4())
        async with self.sessions() as db:
            db.add_all([MediaItem(uuid=self.movie_uuid, title='Movie', year=2024, media_type=MediaType.MOVIE, is_persisted=True),
                        MediaItem(uuid=self.tv_uuid, title='Show', media_type=MediaType.TV, is_persisted=True)])
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()
        self.temp.cleanup()

    async def test_movie_download_links_without_scanning_and_is_idempotent(self):
        async with self.sessions() as db:
            identity = await download_target(db, self.movie_uuid, 'movie')
            write_identity(self.file, identity)
            source = await register_local_file(db, self.file, identity)
            id_ = source.id
            await db.commit()
        async with self.sessions() as db:
            second = await register_local_file(db, self.file, read_identity(self.file))
            self.assertEqual(second.id, id_)
            await db.commit()
            sources = await source_orchestrator.get_sources(db, media_uuid=self.movie_uuid, media_type='movie', title='Movie')
            self.assertEqual(sources['local'][0]['id'], id_)
            self.assertEqual(sources['local'][0]['source_type'], 'local')
            self.assertEqual(sources['local'][0]['file_path'], str(self.file.resolve()))
            self.assertEqual(identity['title'], 'Movie (2024)')

    async def test_episode_download_only_appears_for_correct_episode(self):
        async with self.sessions() as db:
            identity = await download_target(db, self.tv_uuid, 'tv', 2, 3)
            await register_local_file(db, self.file, identity)
            await db.commit()
            item = await db.get(MediaItem, identity['media_uuid'])
            self.assertEqual(item.parent_id, self.tv_uuid)
            self.assertEqual((item.season_number, item.episode_number), (2, 3))
            self.assertIn('S02E03', identity['title'])
            self.assertIn('season=2&episode=3', identity['play_url'])
            sources = await source_orchestrator.get_sources(db, media_uuid=self.tv_uuid, media_type='tv', title='Show', season=2, episode=3)
            self.assertEqual(len(sources['local']), 1)
            result = await db.execute(select(MediaSourceModel).join(MediaItem, MediaSourceModel.media_uuid == MediaItem.uuid).where(MediaItem.parent_id == self.tv_uuid, MediaItem.episode_number == 4))
            self.assertIsNone(result.scalar_one_or_none())
            again = await download_target(db, self.tv_uuid, 'tv', 2, 3)
            self.assertEqual(again['media_uuid'], identity['media_uuid'])

    async def test_completed_job_links_and_startup_reconcile_does_not_duplicate(self):
        service = DownloadService(Path(self.temp.name) / 'downloads')
        async with self.sessions() as db:
            identity = await download_target(db, self.movie_uuid, 'movie')
        with patch('experimental.backend.database.AsyncSessionLocal', self.sessions):
            job = service.start(str(self.file), identity['title'], media=identity)
            await service.tasks[job['id']]
            self.assertEqual(service.jobs[job['id']]['status'], 'completed')
            self.assertIsNotNone(service.jobs[job['id']]['local_source_id'])
            await service.reconcile_library()
        async with self.sessions() as db:
            result = await db.execute(select(MediaSourceModel))
            self.assertEqual(len(result.scalars().all()), 1)
        self.assertEqual(read_identity(service.completed_file(job['id']))['media_uuid'], self.movie_uuid)

    async def test_active_scan_preserves_saved_identity_without_filename_matching(self):
        async with self.sessions() as db:
            identity = await download_target(db, self.tv_uuid, 'tv', 1, 7)
            write_identity(self.file, identity)
            path = LibraryPath(name='Downloads', path=str(self.file.parent), media_type=MediaType.TV)
            db.add(path); await db.flush()
            scanner = ActiveScanner(db)
            with patch.object(scanner.base, 'match_with_tmdb', side_effect=AssertionError('Must use saved identity')):
                await scanner._process_video_file(db, str(self.file), path)
            await db.commit()
            result = await db.execute(select(MediaSourceModel))
            source = result.scalar_one()
            self.assertEqual(source.media_uuid, identity['media_uuid'])
            self.assertEqual(source.library_path_id, path.id)
