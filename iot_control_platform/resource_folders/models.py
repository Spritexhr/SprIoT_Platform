from django.core.exceptions import ValidationError
from django.db import models


class ResourceFolder(models.Model):
    """传感器、设备、自动化规则管理页使用的独立目录树。"""

    SENSOR = "sensor"
    DEVICE = "device"
    AUTOMATION = "automation"
    RESOURCE_TYPE_CHOICES = ((SENSOR, "传感器"), (DEVICE, "设备"), (AUTOMATION, "自动化规则"))

    name = models.CharField(max_length=100, verbose_name="文件夹名称")
    resource_type = models.CharField(
        max_length=10, choices=RESOURCE_TYPE_CHOICES, db_index=True, verbose_name="资源类型"
    )
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT,
        related_name="children", verbose_name="上级文件夹",
    )
    sort_order = models.IntegerField(default=0, db_index=True, verbose_name="显示顺序")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        ordering = ["sort_order", "id"]
        verbose_name = "资源文件夹"
        verbose_name_plural = "资源文件夹"
        constraints = [
            models.UniqueConstraint(
                fields=["resource_type", "parent", "name"],
                name="uniq_resource_folder_sibling_name",
            )
        ]

    def __str__(self):
        names = [self.name]
        current = self.parent
        visited = {self.pk}
        while current is not None and current.pk not in visited:
            visited.add(current.pk)
            names.insert(0, current.name)
            current = current.parent
        return f"{self.get_resource_type_display()}/{' / '.join(names)}"

    def clean(self):
        if self.parent_id:
            if self.parent.resource_type != self.resource_type:
                raise ValidationError({"parent": "父文件夹必须属于同一种资源类型"})
            current = self.parent
            while current is not None:
                if current.pk == self.pk:
                    raise ValidationError({"parent": "不能将文件夹移动到自身或其子文件夹中"})
                current = current.parent
