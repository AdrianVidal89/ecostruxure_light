"""Drop the global ``poc_lead`` role.

Leadership is now a strictly per-POC fact (``POCMembership.role_in_poc``), so the
global role set is reduced to ``admin`` / ``team_member``. Any user who was
globally ``poc_lead`` becomes a ``team_member``; their per-POC lead memberships
are untouched, so they keep leading the POCs they actually lead.
"""

from django.db import migrations, models


def poc_leads_to_team_members(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    User.objects.filter(role="poc_lead").update(role="team_member")


def noop_reverse(apps, schema_editor):
    # The poc_lead role no longer exists; nothing to restore.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(poc_leads_to_team_members, noop_reverse),
        migrations.AlterField(
            model_name="user",
            name="role",
            field=models.CharField(
                choices=[("admin", "Admin"), ("team_member", "Team Member")],
                default="team_member",
                help_text="Global role governing application-wide permissions.",
                max_length=20,
            ),
        ),
    ]
