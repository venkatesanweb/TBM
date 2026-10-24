from django.db import migrations


FORWARD_SQL = """
CREATE OR REPLACE FUNCTION catalog_assert_product_has_boms() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    target_product_id bigint;
BEGIN
    IF TG_TABLE_NAME = 'catalog_product' THEN
        target_product_id := COALESCE(NEW.id, OLD.id);
    ELSIF TG_TABLE_NAME = 'catalog_productrevision' THEN
        target_product_id := COALESCE(NEW.product_id, OLD.product_id);
    ELSE
        IF TG_OP = 'DELETE' THEN
            SELECT product_id INTO target_product_id
            FROM catalog_productrevision
            WHERE id = OLD.product_revision_id;
        ELSE
            SELECT product_id INTO target_product_id
            FROM catalog_productrevision
            WHERE id = NEW.product_revision_id;
        END IF;

        IF TG_OP <> 'DELETE' AND NOT EXISTS (
            SELECT 1 FROM catalog_component
            WHERE id = NEW.component_id AND active = TRUE
        ) THEN
            RAISE EXCEPTION 'Inactive components cannot be added to a BOM';
        END IF;
    END IF;

    IF target_product_id IS NULL OR NOT EXISTS (
        SELECT 1 FROM catalog_product WHERE id = target_product_id
    ) THEN
        RETURN NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM catalog_product product
        JOIN catalog_productrevision current_revision
          ON current_revision.id = product.current_revision_id
         AND current_revision.product_id = product.id
        JOIN catalog_bomline current_line
          ON current_line.product_revision_id = current_revision.id
        WHERE product.id = target_product_id
    ) THEN
        RAISE EXCEPTION 'A product must have a current revision with at least one BOM line';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM catalog_productrevision revision
        LEFT JOIN catalog_bomline line
          ON line.product_revision_id = revision.id
        WHERE revision.product_id = target_product_id
        GROUP BY revision.id
        HAVING COUNT(line.id) = 0
    ) THEN
        RAISE EXCEPTION 'Every product revision must have at least one BOM line';
    END IF;

    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER catalog_product_requires_bom
AFTER INSERT OR UPDATE ON catalog_product
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION catalog_assert_product_has_boms();

CREATE CONSTRAINT TRIGGER catalog_revision_requires_bom
AFTER INSERT OR UPDATE OR DELETE ON catalog_productrevision
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION catalog_assert_product_has_boms();

CREATE CONSTRAINT TRIGGER catalog_bom_requires_product_bom
AFTER INSERT OR UPDATE OR DELETE ON catalog_bomline
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION catalog_assert_product_has_boms();
"""

REVERSE_SQL = """
DROP TRIGGER IF EXISTS catalog_bom_requires_product_bom ON catalog_bomline;
DROP TRIGGER IF EXISTS catalog_revision_requires_bom ON catalog_productrevision;
DROP TRIGGER IF EXISTS catalog_product_requires_bom ON catalog_product;
DROP FUNCTION IF EXISTS catalog_assert_product_has_boms();
"""


def install_postgres_triggers(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(FORWARD_SQL)


def remove_postgres_triggers(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(REVERSE_SQL)


class Migration(migrations.Migration):
    dependencies = [("catalog", "0002_component_category_component_reorder_level_and_more")]

    operations = [
        migrations.RunPython(install_postgres_triggers, remove_postgres_triggers),
    ]
