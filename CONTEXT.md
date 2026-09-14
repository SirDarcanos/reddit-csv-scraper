# Reddit CSV Scraper

Language for a scrape run and the data it produces.

## Language

**Scrape run**:
One attempt to collect submissions and comments from a subreddit over a
requested time range, including resumed work and its final outcome.
_Avoid_: Job, session

**Scrape output**:
The CSV file containing a scrape run’s submission and comment rows plus the
completed-submission state used to resume that run.
_Avoid_: Export, checkpoint file
