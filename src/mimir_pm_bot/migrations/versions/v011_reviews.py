from alembic import op

revision = "011_reviews"
down_revision = "010_incidents"


def upgrade() -> None:
    op.execute("""CREATE TABLE reviews(
        id INTEGER PRIMARY KEY, pile INTEGER NOT NULL REFERENCES piles,
        source INTEGER NOT NULL REFERENCES versions, repo TEXT NOT NULL, pull INTEGER NOT NULL,
        request TEXT NOT NULL, prompt TEXT NOT NULL, prompt_hash TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'held', base TEXT, head TEXT, session TEXT,
        attempt INTEGER NOT NULL DEFAULT 0, due REAL NOT NULL DEFAULT 0, result TEXT,
        UNIQUE(source,pull))""")
    op.execute("""CREATE TABLE review_actions(
        id INTEGER PRIMARY KEY, review INTEGER NOT NULL REFERENCES reviews,
        pile INTEGER NOT NULL REFERENCES piles, source INTEGER NOT NULL REFERENCES versions,
        kind TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'held', marker TEXT NOT NULL UNIQUE,
        url TEXT, due REAL NOT NULL DEFAULT 0, UNIQUE(review,source,kind))""")
    op.execute("""CREATE TRIGGER review_turn_finish AFTER UPDATE OF state ON piles
        WHEN OLD.state='active' AND NEW.state!='active' BEGIN
        UPDATE reviews SET state=CASE WHEN NEW.state='done' AND NEW.detail='end_turn'
            THEN 'pending' ELSE 'cancelled' END WHERE pile=NEW.id AND state='held';
        UPDATE review_actions SET state=CASE WHEN NEW.state='done' AND NEW.detail='end_turn'
            THEN 'pending' ELSE 'cancelled' END WHERE pile=NEW.id AND state='held';
        UPDATE reviews SET state='cancelled' WHERE state IN ('pending','running','ready')
            AND id IN (SELECT review FROM review_actions WHERE pile=NEW.id
            AND kind='cancel' AND state='pending');
        UPDATE review_actions SET state='done' WHERE pile=NEW.id AND kind='cancel'
            AND state='pending';
        END""")


def downgrade() -> None:
    op.execute("DROP TRIGGER review_turn_finish")
    op.drop_table("review_actions")
    op.drop_table("reviews")
