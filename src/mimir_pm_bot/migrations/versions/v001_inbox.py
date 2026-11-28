from alembic import op

revision = "001_inbox"
down_revision = None


def upgrade() -> None:
    op.execute("""CREATE TABLE binding (
      singleton INTEGER PRIMARY KEY CHECK(singleton=1), bot INTEGER NOT NULL,
      chat INTEGER NOT NULL, topic INTEGER NOT NULL, next_offset INTEGER, last_arrival TEXT)""")
    op.execute("""CREATE TABLE inbox (
      update_id INTEGER PRIMARY KEY, arrived TEXT NOT NULL, outcome TEXT NOT NULL)""")
    op.execute("""CREATE TABLE messages (
      chat INTEGER NOT NULL, message INTEGER NOT NULL, topic INTEGER NOT NULL,
      PRIMARY KEY(chat,message))""")
    op.execute("""CREATE TABLE versions (
      update_id INTEGER PRIMARY KEY REFERENCES inbox, chat INTEGER NOT NULL,
      message INTEGER NOT NULL, speaker_kind TEXT NOT NULL, speaker_id INTEGER,
      speaker_name TEXT, text TEXT, caption TEXT, sent TEXT NOT NULL, edited TEXT,
      reply_to INTEGER, is_edit INTEGER NOT NULL,
      FOREIGN KEY(chat,message) REFERENCES messages(chat,message))""")


def downgrade() -> None:
    for table in ("versions", "messages", "inbox", "binding"):
        op.drop_table(table)
