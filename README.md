# pysync
Run-time mirroring of host directory to docker directory.

## Usage
```
from pysync import sync
sync("D:\path\to\sync", "container_name:/path/to/sync")
```
