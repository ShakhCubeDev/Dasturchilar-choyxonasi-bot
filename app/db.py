from __future__ import annotations

import asyncpg
from asyncpg import Pool


class Database:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self.pool: Pool | None = None

    async def connect(self) -> None:
        self.pool = await asyncpg.create_pool(
            dsn=self._dsn,
            min_size=1,
            max_size=10,
            command_timeout=15,
        )

    async def disconnect(self) -> None:
        if self.pool:
            await self.pool.close()

    async def init_schema(self) -> None:
        if not self.pool:
            raise RuntimeError("Database pool is not initialized")
        query = """
        CREATE EXTENSION IF NOT EXISTS pgcrypto;

        CREATE TABLE IF NOT EXISTS groups (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            chat_id BIGINT UNIQUE NOT NULL,
            title VARCHAR(255) NOT NULL,
            owner_telegram_id BIGINT NOT NULL,
            bot_is_admin BOOLEAN NOT NULL DEFAULT FALSE,
            registration_enabled BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_groups_owner ON groups (owner_telegram_id);

        CREATE TABLE IF NOT EXISTS users (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            group_chat_id BIGINT NOT NULL DEFAULT 0,
            telegram_id BIGINT NOT NULL,
            username VARCHAR(100),
            full_name VARCHAR(100),
            phone VARCHAR(20) NOT NULL,
            age INT NOT NULL CHECK (age >= 12 AND age <= 70),
            field VARCHAR(100) NOT NULL,
            profession VARCHAR(100),
            experience VARCHAR(20) NOT NULL,
            language VARCHAR(10) NOT NULL,
            purpose VARCHAR(255),
            status VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'rejected')),
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP NOT NULL DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS idx_users_telegram_id ON users (telegram_id);
        CREATE INDEX IF NOT EXISTS idx_users_status ON users (status);

        CREATE TABLE IF NOT EXISTS join_gates (
            group_chat_id BIGINT NOT NULL,
            telegram_id BIGINT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_join_gates_created_at ON join_gates (created_at);

        CREATE TABLE IF NOT EXISTS spam_mode_settings (
            mode VARCHAR(30) PRIMARY KEY CHECK (mode IN ('dch', 'other_groups')),
            vote_threshold INT NOT NULL DEFAULT 5 CHECK (vote_threshold >= 2 AND vote_threshold <= 50),
            timeout_seconds INT NOT NULL DEFAULT 300 CHECK (timeout_seconds >= 30 AND timeout_seconds <= 3600),
            global_enabled BOOLEAN NOT NULL DEFAULT TRUE,
            updated_at TIMESTAMP NOT NULL DEFAULT NOW()
        );
        INSERT INTO spam_mode_settings (mode, vote_threshold, timeout_seconds, global_enabled)
        VALUES ('dch', 5, 300, TRUE), ('other_groups', 5, 300, TRUE)
        ON CONFLICT (mode) DO NOTHING;

        CREATE TABLE IF NOT EXISTS global_spam_users_mode (
            mode VARCHAR(30) NOT NULL CHECK (mode IN ('dch', 'other_groups')),
            telegram_id BIGINT NOT NULL,
            target_username VARCHAR(100),
            source_group_id BIGINT,
            source_group_title VARCHAR(255),
            source_group_username VARCHAR(255),
            source_poll_id BIGINT,
            reason VARCHAR(255) NOT NULL DEFAULT 'community_vote',
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            PRIMARY KEY (mode, telegram_id)
        );
        CREATE INDEX IF NOT EXISTS idx_global_spam_mode_created_at ON global_spam_users_mode (created_at);

        CREATE TABLE IF NOT EXISTS spam_polls (
            id BIGSERIAL PRIMARY KEY,
            mode VARCHAR(30) NOT NULL DEFAULT 'other_groups' CHECK (mode IN ('dch', 'other_groups')),
            group_chat_id BIGINT NOT NULL,
            target_telegram_id BIGINT NOT NULL,
            initiator_telegram_id BIGINT NOT NULL,
            message_id BIGINT,
            yes_votes INT NOT NULL DEFAULT 0,
            no_votes INT NOT NULL DEFAULT 0,
            threshold INT NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'open',
            decision VARCHAR(30),
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            closed_at TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_spam_polls_open ON spam_polls (status, expires_at);
        CREATE INDEX IF NOT EXISTS idx_spam_polls_group_target ON spam_polls (group_chat_id, target_telegram_id, status);

        CREATE TABLE IF NOT EXISTS spam_poll_votes (
            poll_id BIGINT NOT NULL REFERENCES spam_polls (id) ON DELETE CASCADE,
            voter_id BIGINT NOT NULL,
            vote_yes BOOLEAN NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            PRIMARY KEY (poll_id, voter_id)
        );

        -- Migration: existing users table compatibility for multi-group mode.
        DO $$
        DECLARE c RECORD;
        DECLARE has_profession BOOLEAN;
        DECLARE has_group_chat_id BOOLEAN;
        DECLARE has_updated_at BOOLEAN;
        DECLARE has_join_group_chat_id BOOLEAN;
        DECLARE has_spam_mode BOOLEAN;
        DECLARE has_group_registration_enabled BOOLEAN;
        DECLARE has_target_username BOOLEAN;
        DECLARE has_source_group_title BOOLEAN;
        DECLARE has_source_group_username BOOLEAN;
        BEGIN
          IF to_regclass('public.users') IS NULL THEN
            RETURN;
          END IF;

          FOR c IN
            SELECT conname
            FROM pg_constraint
            WHERE conrelid = 'public.users'::regclass
              AND contype = 'c'
              AND pg_get_constraintdef(oid) LIKE '%age%'
              AND pg_get_constraintdef(oid) LIKE '%<= 70%'
          LOOP
            EXECUTE format('ALTER TABLE public.users DROP CONSTRAINT %I', c.conname);
          END LOOP;

          BEGIN
            ALTER TABLE public.users
              ADD CONSTRAINT users_age_check CHECK (age >= 12 AND age <= 70);
          EXCEPTION
            WHEN duplicate_object THEN
              NULL;
          END;

          BEGIN
            ALTER TABLE public.users
              ALTER COLUMN full_name DROP NOT NULL;
          EXCEPTION
            WHEN undefined_column THEN
              NULL;
          END;

          SELECT EXISTS (
              SELECT 1
              FROM information_schema.columns
              WHERE table_schema='public' AND table_name='users' AND column_name='group_chat_id'
          ) INTO has_group_chat_id;

          IF NOT has_group_chat_id THEN
            ALTER TABLE public.users ADD COLUMN group_chat_id BIGINT NOT NULL DEFAULT 0;
          END IF;

          SELECT EXISTS (
              SELECT 1
              FROM information_schema.columns
              WHERE table_schema='public' AND table_name='users' AND column_name='updated_at'
          ) INTO has_updated_at;

          IF NOT has_updated_at THEN
            ALTER TABLE public.users ADD COLUMN updated_at TIMESTAMP NOT NULL DEFAULT NOW();
          END IF;

          SELECT EXISTS (
              SELECT 1
              FROM information_schema.columns
              WHERE table_schema='public' AND table_name='users' AND column_name='profession'
          ) INTO has_profession;

          IF NOT has_profession THEN
            ALTER TABLE public.users ADD COLUMN profession VARCHAR(100);
          END IF;

          UPDATE public.users
          SET profession = COALESCE(NULLIF(profession, ''), field)
          WHERE profession IS NULL OR profession = '';

          -- Drop old UNIQUE(telegram_id), then enforce per-group uniqueness.
          FOR c IN
            SELECT conname
            FROM pg_constraint
            WHERE conrelid = 'public.users'::regclass
              AND contype = 'u'
              AND pg_get_constraintdef(oid) ILIKE '%(telegram_id)%'
          LOOP
            EXECUTE format('ALTER TABLE public.users DROP CONSTRAINT %I', c.conname);
          END LOOP;

          BEGIN
            ALTER TABLE public.users
              ADD CONSTRAINT users_group_user_unique UNIQUE (group_chat_id, telegram_id);
          EXCEPTION
            WHEN duplicate_object THEN
              NULL;
            WHEN duplicate_table THEN
              NULL;
          END;

          BEGIN
            CREATE INDEX IF NOT EXISTS idx_users_group_status
            ON public.users (group_chat_id, status);
          EXCEPTION
            WHEN undefined_column THEN
              NULL;
          END;

          -- join_gates migration for per-group gating.
          SELECT EXISTS (
              SELECT 1
              FROM information_schema.columns
              WHERE table_schema='public' AND table_name='join_gates' AND column_name='group_chat_id'
          ) INTO has_join_group_chat_id;

          IF NOT has_join_group_chat_id THEN
            ALTER TABLE public.join_gates ADD COLUMN group_chat_id BIGINT NOT NULL DEFAULT 0;
          END IF;

          BEGIN
            ALTER TABLE public.join_gates DROP CONSTRAINT join_gates_pkey;
          EXCEPTION
            WHEN undefined_object THEN
              NULL;
          END;

          BEGIN
            CREATE UNIQUE INDEX IF NOT EXISTS uq_join_gates_group_user
            ON public.join_gates (group_chat_id, telegram_id);
          EXCEPTION
            WHEN undefined_column THEN
              NULL;
          END;

          -- spam_polls migration: add mode if missing.
          SELECT EXISTS (
              SELECT 1
              FROM information_schema.columns
              WHERE table_schema='public' AND table_name='spam_polls' AND column_name='mode'
          ) INTO has_spam_mode;

          IF NOT has_spam_mode AND to_regclass('public.spam_polls') IS NOT NULL THEN
            ALTER TABLE public.spam_polls
              ADD COLUMN mode VARCHAR(30) NOT NULL DEFAULT 'other_groups';
          END IF;

          -- groups migration: registration toggle
          SELECT EXISTS (
              SELECT 1
              FROM information_schema.columns
              WHERE table_schema='public' AND table_name='groups' AND column_name='registration_enabled'
          ) INTO has_group_registration_enabled;

          IF NOT has_group_registration_enabled AND to_regclass('public.groups') IS NOT NULL THEN
            ALTER TABLE public.groups
              ADD COLUMN registration_enabled BOOLEAN NOT NULL DEFAULT TRUE;
          END IF;

          BEGIN
            CREATE INDEX IF NOT EXISTS idx_spam_polls_mode_open
            ON public.spam_polls (mode, status, expires_at);
          EXCEPTION
            WHEN undefined_column THEN
              NULL;
          END;

          -- global_spam_users_mode migration: extra metadata columns.
          SELECT EXISTS (
              SELECT 1
              FROM information_schema.columns
              WHERE table_schema='public' AND table_name='global_spam_users_mode' AND column_name='target_username'
          ) INTO has_target_username;
          IF NOT has_target_username AND to_regclass('public.global_spam_users_mode') IS NOT NULL THEN
            ALTER TABLE public.global_spam_users_mode ADD COLUMN target_username VARCHAR(100);
          END IF;

          SELECT EXISTS (
              SELECT 1
              FROM information_schema.columns
              WHERE table_schema='public' AND table_name='global_spam_users_mode' AND column_name='source_group_title'
          ) INTO has_source_group_title;
          IF NOT has_source_group_title AND to_regclass('public.global_spam_users_mode') IS NOT NULL THEN
            ALTER TABLE public.global_spam_users_mode ADD COLUMN source_group_title VARCHAR(255);
          END IF;

          SELECT EXISTS (
              SELECT 1
              FROM information_schema.columns
              WHERE table_schema='public' AND table_name='global_spam_users_mode' AND column_name='source_group_username'
          ) INTO has_source_group_username;
          IF NOT has_source_group_username AND to_regclass('public.global_spam_users_mode') IS NOT NULL THEN
            ALTER TABLE public.global_spam_users_mode ADD COLUMN source_group_username VARCHAR(255);
          END IF;
        END $$;
        """
        async with self.pool.acquire() as conn:
            await conn.execute(query)
