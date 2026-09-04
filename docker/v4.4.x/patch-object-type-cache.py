"""
Backport NetBox 4.5's ObjectType introspection cache into the 4.4 test image.

NetBox 4.4's ``ObjectTypeManager.get_for_model`` asks Postgres for the full
table list (``connection.introspection.table_names()``) on every call, to
detect a pre-4.4 migration state. Serializers, change logging and event
queueing call it constantly, so the 4.4 test suite spends a third of its time
on that catalog scan and issues ~60% more SQL than 4.5, where NetBox caches the
answer once per process. This applies the same cache, so the 4.4 CI leg
measures the plugin rather than that check. The 4.4 series receives no further
upstream releases, so the fix will not arrive from there.

Build-time only: it rewrites the file inside the image and fails the build if
the expected block is not found, so a base-image change cannot silently turn
the patch into a no-op.
"""

import sys

PATH = "/opt/netbox/netbox/core/models/object_types.py"

OLD_CLASS = """class ObjectTypeManager(models.Manager):

    def get_queryset(self):
"""
NEW_CLASS = """class ObjectTypeManager(models.Manager):

    # Cache the result of introspection to avoid repeated queries.
    _table_exists = False

    def get_queryset(self):
"""

OLD_CHECK = """        if 'core_objecttype' not in connection.introspection.table_names():
            ct = ContentType.objects.get_for_model(model, for_concrete_model=for_concrete_model)
            ct.features = get_model_features(ct.model_class())
            return ct
"""
NEW_CHECK = """        if not ObjectTypeManager._table_exists:
            if 'core_objecttype' not in connection.introspection.table_names():
                ct = ContentType.objects.get_for_model(model, for_concrete_model=for_concrete_model)
                ct.features = get_model_features(ct.model_class())
                return ct
            ObjectTypeManager._table_exists = True
"""

with open(PATH) as fh:
    source = fh.read()

for old in (OLD_CLASS, OLD_CHECK):
    if source.count(old) != 1:
        sys.exit(f"patch-object-type-cache: expected block not found exactly once in {PATH}")

source = source.replace(OLD_CLASS, NEW_CLASS, 1).replace(OLD_CHECK, NEW_CHECK, 1)
with open(PATH, "w") as fh:
    fh.write(source)
print("patch-object-type-cache: applied")
