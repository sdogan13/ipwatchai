# Scheduled Tasks for IP Watch AI

## IDF Computation (Monthly)

The `compute_idf.py` script should run monthly to keep word importance scores up-to-date with the latest trademark data.

### What it does

- Scans all 2.2M+ trademarks in the database
- Computes IDF (Inverse Document Frequency) for each word
- Classifies words into 3 tiers:
  - **GENERIC** (>0.5% of trademarks): weight 0.1
  - **SEMI_GENERIC** (0.1-0.5%): weight 0.5
  - **DISTINCTIVE** (<0.1%): weight 1.0
- Updates the `word_idf` table

### Setup on Windows Task Scheduler

1. Open **Task Scheduler** (search "Task Scheduler" in Start menu)

2. Click **Create Basic Task**

3. Configure:
   - **Name**: `IPWatch_ComputeIDF`
   - **Description**: `Monthly IDF computation for trademark word scoring`
   - **Trigger**: Monthly, Day 1, 3:00 AM
   - **Action**: Start a program
   - **Program**: `C:\Users\701693\turk_patent\scripts\compute_idf_scheduled.bat`
   - **Start in**: `C:\Users\701693\turk_patent`

4. Click **Finish**

### Manual Run

```cmd
cd C:\Users\701693\turk_patent
python compute_idf.py
```

### Verify Last Run

Check the log file:
```cmd
type logs\idf_scheduled.log
```

Or check the database:
```sql
SELECT MAX(updated_at) as last_update, COUNT(*) as word_count
FROM word_idf;
```

### Expected Output

```
Total documents:    2,200,000+
Unique words:       850,000+
GENERIC words:      ~15 (weight=0.1)
SEMI_GENERIC words: ~175 (weight=0.5)
DISTINCTIVE words:  ~850,000 (weight=1.0)
Time elapsed:       ~30 seconds
```
