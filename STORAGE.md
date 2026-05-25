# Storage Awareness and Disk-Space Policy

LJS monitors all configured app-controlled storage roots:

- the global download directory;
- every registered category library root;
- any category path loaded from `config/categories/<category_id>.yaml` into the effective `settings.category_settings` map.

The monitor groups paths by physical/logical volume, so if TV and Movies share the same disk their free space is reported once, while categories on separate disks appear as separate volumes.

## Assistant context

When `settings.storage.include_in_llm_context` is enabled, every assistant run receives a compact storage summary. The assistant can also call:

- `get_storage_status`
- `check_storage_capacity`

These are read-only tools. They are intended for download planning, explaining low-space warnings, and avoiding large downloads on constrained disks.

## Download preflight

Before queueing a magnet, `DownloadManager.add_magnet()` asks the storage monitor for a capacity decision. If an estimated size is available, the projected free space must remain above `settings.storage.minimum_free_after_download_gb`; otherwise the download is blocked with a clear error. Warning-level disks are allowed but logged and surfaced through the UI/context.

## Settings

```yaml
storage:
  enabled: true
  include_in_llm_context: true
  warning_free_percent: 15.0
  critical_free_percent: 5.0
  warning_free_gb: 50.0
  critical_free_gb: 10.0
  minimum_free_after_download_gb: 5.0
  context_max_volumes: 5
  ui_refresh_seconds: 60
```
