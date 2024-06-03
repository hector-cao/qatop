# qatop

Collection of tools for Intel QAT (Quick Assist Technology)

To show config for a set of devices:

```
sudo ./qatctl --status --devices 70:00.0
```

To show only data for a set of devices:

```
sudo ./qatctl --status --devices 70:00.0
```

To show only data for a set of counters:

```
sudo ./qatop  --devices 70:00.0 --counters util_pke
```

To output data in terminal:

```
sudo ./qatop --record  --devices 70:00.0 --counters util_pke exec_pke
```
