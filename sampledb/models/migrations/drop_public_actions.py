# coding: utf-8
"""
Replace the public_actions table with the all_user_action_permissions table.
"""

import os

MIGRATION_INDEX = 95
MIGRATION_NAME, _ = os.path.splitext(os.path.basename(__file__))


def run(db):
    # Skip migration by condition
    table_exists = db.session.execute("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_name = 'public_actions'
    """).fetchall()
    if not table_exists:
        return False

    # Perform migration
    public_actions = db.session.execute("""
        SELECT action_id
        FROM public_actions
    """).fetchall()
    all_user_action_permissions = db.session.execute("""
        SELECT action_id, permissions
        FROM all_user_action_permissions
    """).fetchall()
    all_user_action_permissions = {
        action_id: permissions
        for action_id, permissions in all_user_action_permissions
    }
    for action_id, in public_actions:
        if action_id not in all_user_action_permissions:
            db.session.execute("""
                INSERT INTO all_user_action_permissions
                (action_id, permissions)
                VALUES (:action_id, 'READ')
            """, {'action_id': action_id})
    db.session.execute("""
        DROP TABLE public_actions
    """)
    return True
