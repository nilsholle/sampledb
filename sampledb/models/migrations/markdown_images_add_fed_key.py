# coding: utf-8
"""
Add component_id and id columns, fed key unique constraint and id primary key constraint to markdown_images table.
"""

import os

MIGRATION_INDEX = 92
MIGRATION_NAME, _ = os.path.splitext(os.path.basename(__file__))


def run(db):
    # Skip migration by condition
    column_names = db.session.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'markdown_images'
    """).fetchall()
    if ('component_id',) in column_names:
        return False

    # Perform migration
    db.session.execute("""
        ALTER TABLE markdown_images
            ADD component_id INTEGER,
            ADD FOREIGN KEY(component_id) REFERENCES components(id),
            DROP CONSTRAINT markdown_images_pkey,
            ADD COLUMN id SERIAL PRIMARY KEY,
            ADD CONSTRAINT markdown_images_file_name_component_id_key UNIQUE(file_name, component_id)
    """)
    return True
