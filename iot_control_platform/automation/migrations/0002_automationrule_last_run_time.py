from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('automation', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='automationrule',
            name='last_run_time',
            field=models.DateTimeField(blank=True, help_text='后台轮询调度器记录的最后一次执行时间', null=True, verbose_name='最后执行时间'),
        ),
    ]
