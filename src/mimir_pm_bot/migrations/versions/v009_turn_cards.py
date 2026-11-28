from alembic import op

revision = "009_turn_cards"
down_revision = "008_attempts"


def upgrade() -> None:
    op.execute("""CREATE TABLE turn_cards(
        pile INTEGER PRIMARY KEY REFERENCES piles, message INTEGER, activity TEXT,
        steps INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 1,
        delivered INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT 'ready',
        due REAL NOT NULL DEFAULT 0, failures INTEGER NOT NULL DEFAULT 0)""")
    op.execute("""CREATE TRIGGER turn_card_open AFTER UPDATE OF state ON piles
        WHEN NEW.state='active' AND OLD.state='waiting'
        BEGIN INSERT INTO turn_cards(pile) VALUES(NEW.id); END""")
    op.execute("""CREATE TRIGGER turn_card_finish AFTER UPDATE OF state ON piles
        WHEN OLD.state='active' AND NEW.state!='active'
        BEGIN UPDATE turn_cards SET revision=revision+1 WHERE pile=NEW.id; END""")


def downgrade() -> None:
    op.execute("DROP TRIGGER turn_card_finish")
    op.execute("DROP TRIGGER turn_card_open")
    op.drop_table("turn_cards")
