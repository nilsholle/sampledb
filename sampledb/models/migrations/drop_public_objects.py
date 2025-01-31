# coding: utf-8
"""
Replace the public_objects table with the all_user_object_permissions table.
"""

import os

MIGRATION_INDEX = 96
MIGRATION_NAME, _ = os.path.splitext(os.path.basename(__file__))


def run(db):
    # Skip migration by condition
    table_exists = db.session.execute("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_name = 'public_objects'
    """).fetchall()
    if not table_exists:
        return False

    # Perform migration
    public_objects = db.session.execute("""
        SELECT object_id
        FROM public_objects
    """).fetchall()
    all_user_object_permissions = db.session.execute("""
        SELECT object_id, permissions
        FROM all_user_object_permissions
    """).fetchall()
    all_user_object_permissions = {
        object_id: permissions
        for object_id, permissions in all_user_object_permissions
    }
    for object_id, in public_objects:
        if object_id not in all_user_object_permissions:
            db.session.execute("""
                INSERT INTO all_user_object_permissions
                (object_id, permissions)
                VALUES (:object_id, 'READ')
            """, {'object_id': object_id})
    view_exists = db.session.execute("""
        SELECT table_name
        FROM information_schema.views
        WHERE table_name = 'user_object_permissions_by_all'
    """).fetchall()
    if view_exists:
        # the view will be recreated by the following migration, until then it
        # needs to be dropped as it likely depends on public_objects
        db.session.execute("""
            DROP VIEW user_object_permissions_by_all
        """)
    db.session.execute("""
        DROP TABLE public_objects
    """)
    return True
