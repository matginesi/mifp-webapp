PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS roles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    label TEXT
);

CREATE TABLE IF NOT EXISTS assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT UNIQUE,
    filename TEXT NOT NULL,
    original_filename TEXT,
    path TEXT NOT NULL UNIQUE,
    mime_type TEXT,
    size INTEGER,
    kind TEXT NOT NULL DEFAULT 'other' CHECK(kind IN ('image','document','pdf','video','other')),
    alt_text TEXT,
    caption TEXT,
    source_url TEXT,
    storage_status TEXT NOT NULL DEFAULT 'local' CHECK(storage_status IN ('local','external','missing')),
    is_external INTEGER NOT NULL DEFAULT 0 CHECK(is_external IN (0,1)),
    width INTEGER,
    height INTEGER,
    duration_seconds REAL,
    checksum TEXT UNIQUE,
    content_sha256 TEXT,
    source_url_sha256 TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS asset_recovery_state (
    asset_id INTEGER PRIMARY KEY,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TEXT,
    next_attempt_at TEXT,
    last_error TEXT,
    terminal INTEGER NOT NULL DEFAULT 0 CHECK(terminal IN (0,1)),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_asset_recovery_ready
    ON asset_recovery_state(terminal, next_attempt_at, attempts);
CREATE TRIGGER IF NOT EXISTS reset_asset_recovery_after_source_change
AFTER UPDATE OF source_url ON assets
WHEN COALESCE(OLD.source_url,'') != COALESCE(NEW.source_url,'')
BEGIN
    DELETE FROM asset_recovery_state WHERE asset_id=NEW.id;
END;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    checksum TEXT,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS source_systems (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'other',
    base_url TEXT,
    description TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS source_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL UNIQUE,
    source_system_id INTEGER,
    scraper_version TEXT,
    parser_version TEXT,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    status TEXT NOT NULL DEFAULT 'running' CHECK(status IN ('running','completed','completed_with_errors','failed')),
    source_snapshot_sha256 TEXT,
    stats_json TEXT,
    notes TEXT,
    FOREIGN KEY (source_system_id) REFERENCES source_systems(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS source_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL UNIQUE,
    source_run_id INTEGER,
    source_system_id INTEGER,
    external_id TEXT,
    source_url TEXT,
    source_path TEXT,
    fetched_at TEXT,
    raw_sha256 TEXT,
    raw_payload TEXT NOT NULL,
    record_type TEXT,
    mapping_status TEXT NOT NULL DEFAULT 'unmapped' CHECK(mapping_status IN ('unmapped','mapped','ignored','error')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (source_run_id) REFERENCES source_runs(id) ON DELETE SET NULL,
    FOREIGN KEY (source_system_id) REFERENCES source_systems(id) ON DELETE SET NULL,
    UNIQUE(source_system_id, external_id, raw_sha256)
);

CREATE TABLE IF NOT EXISTS canonical_mappings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_record_id INTEGER NOT NULL,
    entity_type TEXT NOT NULL,
    entity_uid TEXT NOT NULL,
    mapping_kind TEXT NOT NULL DEFAULT 'canonical' CHECK(mapping_kind IN ('canonical','duplicate','related','rejected')),
    confidence REAL,
    decision_note TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (source_record_id) REFERENCES source_records(id) ON DELETE CASCADE,
    UNIQUE(source_record_id, entity_type, entity_uid, mapping_kind)
);

CREATE INDEX IF NOT EXISTS idx_source_records_run ON source_records(source_run_id, mapping_status);
CREATE INDEX IF NOT EXISTS idx_source_records_url ON source_records(source_url);
CREATE INDEX IF NOT EXISTS idx_canonical_mappings_entity ON canonical_mappings(entity_type, entity_uid);

CREATE TABLE IF NOT EXISTS import_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    source_kind TEXT,
    source_path TEXT,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    status TEXT NOT NULL DEFAULT 'running' CHECK(status IN ('running','completed','completed_with_errors','failed')),
    stats_json TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS import_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    import_run_id INTEGER,
    entity_type TEXT,
    entity_id INTEGER,
    source_url TEXT,
    source_path TEXT,
    content_hash TEXT,
    raw_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (import_run_id) REFERENCES import_runs(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT UNIQUE,
    slug TEXT UNIQUE,
    first_name TEXT,
    last_name TEXT,
    display_name TEXT NOT NULL,
    affiliation TEXT,
    country TEXT,
    email TEXT,
    role_id INTEGER,
    field TEXT,
    bio TEXT,
    normalized_name TEXT,
    normalized_affiliation TEXT,
    review_status TEXT NOT NULL DEFAULT 'published' CHECK(review_status IN ('published','draft','review','quarantined','duplicate')),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)),
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (role_id) REFERENCES roles(id)
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT UNIQUE,
    slug TEXT UNIQUE,
    title TEXT NOT NULL,
    start_date TEXT,
    end_date TEXT,
    date_text TEXT,
    date_precision TEXT NOT NULL DEFAULT 'unknown' CHECK(date_precision IN ('day','month','year','range','unknown')),
    location TEXT,
    description TEXT,
    event_type TEXT NOT NULL DEFAULT 'other' CHECK(event_type IN ('conference','workshop','seminar','meeting','school','project_event','other')),
    series_key TEXT,
    parent_event_id INTEGER,
    review_status TEXT NOT NULL DEFAULT 'published' CHECK(review_status IN ('published','draft','review','quarantined','duplicate')),
    is_featured INTEGER NOT NULL DEFAULT 0 CHECK(is_featured IN (0,1)),
    remote_url TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (parent_event_id) REFERENCES events(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS news (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT UNIQUE,
    slug TEXT UNIQUE,
    title TEXT NOT NULL,
    news_type TEXT NOT NULL DEFAULT 'general' CHECK(news_type IN ('general','announcement','publication_highlight','agreement','award','event_highlight','institutional','sponsor','memorial','science_commentary')),
    card_layout TEXT,
    date TEXT,
    date_text TEXT,
    date_precision TEXT NOT NULL DEFAULT 'unknown' CHECK(date_precision IN ('day','month','year','range','unknown')),
    date_is_inferred INTEGER NOT NULL DEFAULT 0 CHECK(date_is_inferred IN (0,1)),
    date_inference_rule TEXT,
    original_date_text TEXT,
    summary TEXT,
    body TEXT,
    review_status TEXT NOT NULL DEFAULT 'published' CHECK(review_status IN ('published','draft','review','quarantined','duplicate')),
    is_featured INTEGER NOT NULL DEFAULT 0 CHECK(is_featured IN (0,1)),
    source_kind TEXT NOT NULL DEFAULT 'manual',
    source_priority INTEGER NOT NULL DEFAULT 50,
    source_order INTEGER NOT NULL DEFAULT 0,
    display_order INTEGER,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS publications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT UNIQUE,
    slug TEXT UNIQUE,
    title TEXT NOT NULL,
    year INTEGER,
    authors TEXT,
    journal TEXT,
    doi TEXT,
    abstract TEXT,
    date_text TEXT,
    date_precision TEXT NOT NULL DEFAULT 'year' CHECK(date_precision IN ('day','month','year','range','unknown')),
    review_status TEXT NOT NULL DEFAULT 'published' CHECK(review_status IN ('published','draft','review','quarantined','duplicate')),
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS research_areas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT UNIQUE,
    slug TEXT UNIQUE,
    title TEXT NOT NULL,
    summary TEXT,
    description TEXT,
    review_status TEXT NOT NULL DEFAULT 'published' CHECK(review_status IN ('published','draft','review','quarantined','duplicate')),
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT UNIQUE,
    slug TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'custom' CHECK(type IN ('about','privacy','cookie_policy','manifesto','code_of_conduct','documentation','custom','legacy_home','contact','error_page')),
    summary TEXT,
    body TEXT,
    version TEXT,
    effective_date TEXT,
    nav_group TEXT,
    menu_order INTEGER NOT NULL DEFAULT 0,
    review_status TEXT NOT NULL DEFAULT 'published' CHECK(review_status IN ('published','draft','review','quarantined','duplicate')),
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sponsors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT UNIQUE,
    slug TEXT UNIQUE,
    name TEXT NOT NULL,
    description TEXT,
    sponsor_type TEXT NOT NULL DEFAULT 'sponsor',
    tier TEXT,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)),
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS asset_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id INTEGER NOT NULL,
    entity_type TEXT NOT NULL CHECK(entity_type IN ('member','event','news','publication','research_area','page','sponsor')),
    entity_id INTEGER NOT NULL,
    role TEXT NOT NULL DEFAULT 'attachment' CHECK(role IN ('cover','gallery','attachment','logo','document','profile')),
    is_primary INTEGER NOT NULL DEFAULT 0 CHECK(is_primary IN (0,1)),
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE,
    UNIQUE(asset_id, entity_type, entity_id, role)
);

CREATE TABLE IF NOT EXISTS entity_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL CHECK(entity_type IN ('member','event','news','publication','research_area','page','sponsor')),
    entity_id INTEGER NOT NULL,
    url TEXT NOT NULL,
    label TEXT,
    role TEXT NOT NULL DEFAULT 'reference',
    is_primary INTEGER NOT NULL DEFAULT 0 CHECK(is_primary IN (0,1)),
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(entity_type, entity_id, url, role)
);

CREATE TABLE IF NOT EXISTS entity_relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL,
    source_id INTEGER NOT NULL,
    target_type TEXT NOT NULL,
    target_id INTEGER NOT NULL,
    role TEXT NOT NULL DEFAULT 'related',
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_type, source_id, target_type, target_id, role)
);

CREATE TABLE IF NOT EXISTS join_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    email TEXT NOT NULL,
    affiliation TEXT,
    country TEXT,
    field TEXT,
    position TEXT,
    orcid TEXT,
    website_url TEXT,
    motivation TEXT,
    invitation_code TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','in_review','approved','rejected','archived')),
    admin_notes TEXT,
    decision_note TEXT,
    source_ip TEXT,
    user_agent TEXT,
    member_id INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reviewed_at TEXT,
    reviewed_by TEXT,
    FOREIGN KEY (member_id) REFERENCES members(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS page_views (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    method TEXT NOT NULL DEFAULT 'GET',
    client_ip TEXT,
    user_agent_hash TEXT,
    user TEXT DEFAULT '-',
    status INTEGER NOT NULL DEFAULT 200,
    duration_ms REAL NOT NULL DEFAULT 0,
    referrer TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS metrics_daily (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    scope TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    metric_key TEXT NOT NULL DEFAULT '',
    metric_value INTEGER NOT NULL DEFAULT 0,
    extra_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(date, scope, metric_name, metric_key)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_members_role ON members(role_id);
CREATE INDEX IF NOT EXISTS idx_members_active ON members(is_active, sort_order);
CREATE INDEX IF NOT EXISTS idx_events_dates ON events(start_date, end_date);
CREATE INDEX IF NOT EXISTS idx_events_review ON events(review_status, start_date, end_date);
CREATE INDEX IF NOT EXISTS idx_news_date ON news(date);
CREATE INDEX IF NOT EXISTS idx_news_review ON news(review_status, date, sort_order);
CREATE INDEX IF NOT EXISTS idx_publications_year ON publications(year);
CREATE INDEX IF NOT EXISTS idx_publications_review ON publications(review_status, year);
CREATE INDEX IF NOT EXISTS idx_research_review ON research_areas(review_status, sort_order);
CREATE INDEX IF NOT EXISTS idx_pages_type ON pages(type, review_status);
CREATE INDEX IF NOT EXISTS idx_assets_kind ON assets(kind);
CREATE INDEX IF NOT EXISTS idx_assets_source_url ON assets(source_url);
CREATE INDEX IF NOT EXISTS idx_asset_links_entity ON asset_links(entity_type, entity_id, role);
CREATE INDEX IF NOT EXISTS idx_entity_links_entity ON entity_links(entity_type, entity_id, role);
CREATE INDEX IF NOT EXISTS idx_entity_relations_source ON entity_relations(source_type, source_id, role);
CREATE INDEX IF NOT EXISTS idx_sponsors_active ON sponsors(is_active, sort_order);
CREATE INDEX IF NOT EXISTS idx_import_records_entity ON import_records(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_join_requests_status ON join_requests(status, created_at);
CREATE INDEX IF NOT EXISTS idx_join_requests_email ON join_requests(email);
CREATE INDEX IF NOT EXISTS idx_page_views_created ON page_views(created_at);
CREATE INDEX IF NOT EXISTS idx_page_views_path ON page_views(path);
CREATE INDEX IF NOT EXISTS idx_metrics_daily_date ON metrics_daily(date);
CREATE INDEX IF NOT EXISTS idx_metrics_daily_scope ON metrics_daily(scope, metric_name);
CREATE INDEX IF NOT EXISTS idx_metrics_daily_key ON metrics_daily(metric_key);

-- Data quality: findings are proposals; only reviewed bundles can mutate data.
CREATE TABLE IF NOT EXISTS quality_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    status TEXT NOT NULL CHECK(status IN ('pending','running','completed','failed')),
    fingerprint TEXT NOT NULL,
    summary_json TEXT NOT NULL DEFAULT '{}',
    error_message TEXT,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    duration_ms INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS quality_findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    action_type TEXT NOT NULL CHECK(action_type IN (
        'clean_record','enrich_record','merge_records',
        'split_aggregated_record','repair_relations_or_assets'
    )),
    entity_type TEXT NOT NULL CHECK(entity_type IN ('member','event','news','publication','research_area','page','sponsor','asset')),
    record_ids_json TEXT NOT NULL,
    classification TEXT NOT NULL CHECK(classification IN (
        'exact_duplicate','strong_candidate','ambiguous',
        'related_not_duplicate','blocked','invalid_record',
        'needs_cleaning','aggregated_record','keep_separate',
        'junk_technical_record','page_fragment_attached'
    )),
    score REAL NOT NULL DEFAULT 0,
    evidence_json TEXT NOT NULL DEFAULT '[]',
    contradictions_json TEXT NOT NULL DEFAULT '[]',
    plan_json TEXT NOT NULL DEFAULT '{}',
    fingerprint TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','bundled','resolved','rejected','deferred')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES quality_runs(id) ON DELETE CASCADE,
    UNIQUE(run_id, fingerprint)
);

CREATE TABLE IF NOT EXISTS merge_exclusions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    record_fingerprint TEXT NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN ('keep_separate','same_series','false_positive','ignored_test_data')),
    note TEXT,
    created_by TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(entity_type, record_fingerprint, decision)
);

CREATE TABLE IF NOT EXISTS quality_bundles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','validated','applying','applied','failed')),
    validation_json TEXT NOT NULL DEFAULT '{}',
    report_json TEXT NOT NULL DEFAULT '{}',
    backup_path TEXT,
    created_by TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    applied_at TEXT
);

CREATE TABLE IF NOT EXISTS quality_bundle_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bundle_id INTEGER NOT NULL,
    finding_id INTEGER NOT NULL,
    action_type TEXT NOT NULL CHECK(action_type IN (
        'clean_record','enrich_record','merge_records',
        'split_aggregated_record','repair_relations_or_assets'
    )),
    payload_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','validated','applied','failed')),
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (bundle_id) REFERENCES quality_bundles(id) ON DELETE CASCADE,
    FOREIGN KEY (finding_id) REFERENCES quality_findings(id) ON DELETE RESTRICT,
    UNIQUE(bundle_id, finding_id)
);

CREATE TABLE IF NOT EXISTS content_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL CHECK(entity_type IN ('member','event','news','publication','research_area','page','sponsor')),
    old_slug TEXT NOT NULL,
    canonical_entity_id INTEGER NOT NULL,
    canonical_slug TEXT NOT NULL,
    bundle_id INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (bundle_id) REFERENCES quality_bundles(id) ON DELETE SET NULL,
    UNIQUE(entity_type, old_slug)
);

CREATE INDEX IF NOT EXISTS idx_quality_runs_status ON quality_runs(status, started_at);
CREATE INDEX IF NOT EXISTS idx_quality_findings_run ON quality_findings(run_id, action_type, classification, score);
CREATE INDEX IF NOT EXISTS idx_quality_bundles_status ON quality_bundles(status, updated_at);
CREATE INDEX IF NOT EXISTS idx_content_aliases_lookup ON content_aliases(entity_type, old_slug);

CREATE TABLE IF NOT EXISTS resolved_pairs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    left_fingerprint TEXT NOT NULL,
    right_fingerprint TEXT NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('merged','rejected','cleaned','enriched','split')),
    finding_id INTEGER,
    bundle_id INTEGER,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(entity_type, left_fingerprint, right_fingerprint)
);

CREATE TABLE IF NOT EXISTS conference_sites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    acronym TEXT,
    year INTEGER,
    status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','ready','archived')),
    start_date TEXT,
    end_date TEXT,
    venue TEXT,
    city TEXT,
    country TEXT,
    canonical_url TEXT,
    deploy_base_path TEXT NOT NULL DEFAULT '/',
    registration_url TEXT,
    contact_email TEXT,
    description TEXT,
    config_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS conference_people (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conference_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    email TEXT,
    affiliation TEXT,
    country TEXT,
    role TEXT NOT NULL DEFAULT 'participant',
    contribution_title TEXT,
    bio TEXT,
    website_url TEXT,
    photo_path TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (conference_id) REFERENCES conference_sites(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_conference_people_site
    ON conference_people(conference_id, sort_order, name);

CREATE TABLE IF NOT EXISTS conference_assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conference_id INTEGER NOT NULL,
    filename TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'gallery' CHECK(role IN (
        'hero_logo','speaker_photo','sponsor_logo','program_source',
        'document','gallery'
    )),
    label TEXT,
    person_id INTEGER,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (conference_id) REFERENCES conference_sites(id) ON DELETE CASCADE,
    FOREIGN KEY (person_id) REFERENCES conference_people(id) ON DELETE SET NULL,
    UNIQUE(conference_id, filename)
);

CREATE INDEX IF NOT EXISTS idx_conference_assets_site
    ON conference_assets(conference_id, role, sort_order, filename);
