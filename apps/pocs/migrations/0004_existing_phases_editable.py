"""Preserve current behaviour: phases that already existed before the hierarchy
feature stay editable by POC leads (newly inherited phases get their flag from
the blueprint)."""

from django.db import migrations


def make_existing_editable(apps, schema_editor):
    Phase = apps.get_model("pocs", "Phase")
    # At this point all phases are pre-feature ones; mark them lead-editable.
    Phase.objects.update(lead_editable=True)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("pocs", "0003_phase_lead_editable_phase_parent_phasetemplate_and_more"),
    ]

    operations = [migrations.RunPython(make_existing_editable, noop)]
