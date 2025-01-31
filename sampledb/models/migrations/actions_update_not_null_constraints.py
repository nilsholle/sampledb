# coding: utf-8
"""
Replace NOT NULL constraints per column by NOT NULL constraints conditioned by federation reference in actions.
"""

import os

MIGRATION_INDEX = 80
MIGRATION_NAME, _ = os.path.splitext(os.path.basename(__file__))


def run(db):
    # Skip migration by condition
    constraints = db.session.execute("""
             SELECT conname
             FROM pg_catalog.pg_constraint
             WHERE conname = 'actions_not_null_check'
        """).fetchall()
    if len(constraints) > 0:
        return False

    # Perform migration
    db.session.execute("""
        ALTER TABLE actions
            ADD CONSTRAINT actions_not_null_check
                CHECK ((
                    fed_id IS NOT NULL AND
                    component_id IS NOT NULL
                ) OR (
                    type_id IS NOT NULL AND
                    schema IS NOT NULL
                )),
            ALTER COLUMN type_id DROP NOT NULL,
            ALTER COLUMN schema DROP NOT NULL
    """)
    return True
