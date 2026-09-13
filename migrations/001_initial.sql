CREATE TABLE IF NOT EXISTS items (
 id TEXT PRIMARY KEY, canonical_url TEXT NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
 source TEXT NOT NULL, source_id TEXT NOT NULL, source_url TEXT NOT NULL, launched_at TIMESTAMPTZ,
 first_seen_at TIMESTAMPTZ NOT NULL, last_seen_at TIMESTAMPTZ NOT NULL, topics JSONB NOT NULL DEFAULT '[]',
 sources JSONB NOT NULL DEFAULT '[]', metrics JSONB NOT NULL DEFAULT '{}', velocity JSONB NOT NULL DEFAULT '{}',
 cluster_id TEXT, raw_score DOUBLE PRECISION NOT NULL DEFAULT 0, llm_decision TEXT, llm_summary TEXT,
 llm_tags JSONB NOT NULL DEFAULT '[]', final_score DOUBLE PRECISION NOT NULL DEFAULT 0,
 status TEXT NOT NULL DEFAULT 'new', UNIQUE(source, source_id));
CREATE INDEX IF NOT EXISTS items_score_idx ON items(final_score DESC);
CREATE TABLE IF NOT EXISTS source_observations (id BIGSERIAL PRIMARY KEY, item_id TEXT REFERENCES items(id) ON DELETE CASCADE, source TEXT NOT NULL, source_id TEXT NOT NULL, observed_at TIMESTAMPTZ NOT NULL, metrics JSONB NOT NULL DEFAULT '{}', raw JSONB NOT NULL DEFAULT '{}');
CREATE INDEX IF NOT EXISTS source_observations_identity_idx ON source_observations(source, source_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS items_canonical_url_idx ON items(canonical_url);
CREATE TABLE IF NOT EXISTS runs (id BIGSERIAL PRIMARY KEY, source TEXT NOT NULL, status TEXT NOT NULL, item_count INTEGER NOT NULL DEFAULT 0, error TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS dead_letters (id BIGSERIAL PRIMARY KEY, source TEXT, error TEXT NOT NULL, payload JSONB NOT NULL DEFAULT '{}', created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS digest_entries (id BIGSERIAL PRIMARY KEY, item_id TEXT REFERENCES items(id), published_at TIMESTAMPTZ NOT NULL, payload JSONB NOT NULL);
CREATE TABLE IF NOT EXISTS feedback (id BIGSERIAL PRIMARY KEY, item_id TEXT REFERENCES items(id), label TEXT CHECK(label IN ('keep','meh','spam')), created_at TIMESTAMPTZ NOT NULL DEFAULT now());
