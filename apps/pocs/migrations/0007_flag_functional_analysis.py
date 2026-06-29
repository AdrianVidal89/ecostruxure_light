"""Flag any existing phase / blueprint node named 'Functional Analysis' as a
functional-analysis phase (matches the current data). Reversible: clears it."""

from django.db import migrations


def flag(apps, schema_editor):
    for model in ("PhaseTemplate", "Phase"):
        Model = apps.get_model("pocs", model)
        Model.objects.filter(name__iexact="Functional Analysis").update(
            is_functional_analysis=True
        )


def unflag(apps, schema_editor):
    for model in ("PhaseTemplate", "Phase"):
        Model = apps.get_model("pocs", model)
        Model.objects.filter(name__iexact="Functional Analysis").update(
            is_functional_analysis=False
        )


class Migration(migrations.Migration):
    dependencies = [
        ("pocs", "0006_functionalanalysisstep_phase_is_functional_analysis_and_more"),
    ]

    operations = [migrations.RunPython(flag, unflag)]
