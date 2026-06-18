import sqlite3
from pathlib import Path
from typing import List

MIGRATIONS: List[str] = [
    # Version 1 migration
    """
    -- Runs table
    CREATE TABLE runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        goal TEXT NOT NULL,
        intake_mode TEXT NOT NULL,
        status TEXT NOT NULL,
        config_snapshot TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- Features table
    CREATE TABLE features (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        outcome TEXT,
        acceptance_criteria TEXT,
        dependencies TEXT, -- JSON array of parent feature names or IDs
        risk TEXT NOT NULL,
        review_status TEXT,
        FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
    );

    -- Tasks table
    CREATE TABLE tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL,
        feature_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        role TEXT NOT NULL,
        dependencies TEXT, -- JSON array of parent task names or IDs
        scope TEXT,
        risk TEXT NOT NULL,
        required_verification TEXT,
        status TEXT NOT NULL,
        FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE,
        FOREIGN KEY(feature_id) REFERENCES features(id) ON DELETE CASCADE
    );

    -- Attempts table
    CREATE TABLE attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL,
        task_id INTEGER NOT NULL,
        route TEXT,
        provider TEXT,
        model TEXT,
        reasoning_level TEXT,
        worktree_path TEXT,
        commit_sha TEXT,
        logs_path TEXT,
        outcome TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE,
        FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
    );

    -- Test runs table
    CREATE TABLE test_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL,
        task_id INTEGER,
        attempt_id INTEGER,
        command TEXT NOT NULL,
        scope TEXT,
        exit_status INTEGER,
        duration_seconds REAL,
        output_path TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE,
        FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE SET NULL,
        FOREIGN KEY(attempt_id) REFERENCES attempts(id) ON DELETE SET NULL
    );

    -- Reviews table
    CREATE TABLE reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL,
        subject_type TEXT NOT NULL,
        subject_id INTEGER NOT NULL,
        reviewer_route TEXT,
        findings TEXT,
        decision TEXT NOT NULL,
        evidence_paths TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
    );

    -- Provider state table
    CREATE TABLE provider_state (
        provider TEXT PRIMARY KEY,
        capability_snapshot TEXT,
        availability INTEGER NOT NULL DEFAULT 1,
        quota_limit_reset TIMESTAMP,
        last_probe TIMESTAMP
    );

    -- Notifications table
    CREATE TABLE notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL,
        event TEXT NOT NULL,
        destination TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        delivery_status TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
    );

    -- Decisions table
    CREATE TABLE decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL,
        decision_type TEXT NOT NULL,
        is_autonomous INTEGER NOT NULL DEFAULT 0,
        summary TEXT NOT NULL,
        details TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
    );

    -- Test migrations table
    CREATE TABLE test_migrations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL,
        task_id INTEGER,
        old_test_path TEXT NOT NULL,
        replacement_test_path TEXT NOT NULL,
        rationale TEXT NOT NULL,
        evidence TEXT,
        approval_status TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE,
        FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE SET NULL
    );
    """,
    # Version 2 migration: upgrade provider_state to compound key (provider, model)
    """
    -- Rename the old table
    ALTER TABLE provider_state RENAME TO provider_state_old;

    -- Create new provider_state table with compound key
    CREATE TABLE provider_state (
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        capability_snapshot TEXT,
        availability INTEGER NOT NULL DEFAULT 1,
        quota_state TEXT NOT NULL DEFAULT 'available',
        quota_limit_reset TIMESTAMP,
        last_probe TIMESTAMP,
        PRIMARY KEY (provider, model)
    );

    -- Insert existing data conservatively
    INSERT INTO provider_state (provider, model, capability_snapshot, availability, quota_state, quota_limit_reset, last_probe)
    SELECT provider, '', capability_snapshot, availability, 'available', quota_limit_reset, last_probe FROM provider_state_old;

    -- Drop the old table
    DROP TABLE provider_state_old;
    """,
    # Version 3 migration: add patch_path to attempts table
    """
    ALTER TABLE attempts ADD COLUMN patch_path TEXT;
    """,
    # Version 4 migration: add previous_behavior, replacement_behavior, and commit_sha to test_migrations table
    """
    ALTER TABLE test_migrations ADD COLUMN previous_behavior TEXT;
    ALTER TABLE test_migrations ADD COLUMN replacement_behavior TEXT;
    ALTER TABLE test_migrations ADD COLUMN commit_sha TEXT;
    """
]

def get_connection(db_path: Path) -> sqlite3.Connection:
    """Gets an SQLite connection and enables foreign key constraints."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    return conn

def migrate(conn: sqlite3.Connection) -> None:
    """Runs all versioned migrations transactionally."""
    # Ensure the migrations metadata table exists
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.commit()

    old_isolation = conn.isolation_level
    conn.isolation_level = None  # Disable python auto-transactions for explicit management
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT version FROM schema_migrations ORDER BY version ASC;")
        applied = {row[0] for row in cursor.fetchall()}

        for i, migration_sql in enumerate(MIGRATIONS, start=1):
            if i not in applied:
                try:
                    cursor.execute("BEGIN TRANSACTION;")
                    # Execute individual statements to preserve transaction boundary
                    statements = [stmt.strip() for stmt in migration_sql.split(";") if stmt.strip()]
                    for stmt in statements:
                        cursor.execute(stmt)
                    cursor.execute(
                        "INSERT INTO schema_migrations (version) VALUES (?);", (i,)
                    )
                    cursor.execute("COMMIT;")
                except Exception as e:
                    cursor.execute("ROLLBACK;")
                    raise RuntimeError(f"Migration version {i} failed: {e}") from e
    finally:
        conn.isolation_level = old_isolation

