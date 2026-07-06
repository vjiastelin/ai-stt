"""Error taxonomy for the job pipeline (spec §3.5)."""


class InfrastructureError(Exception):
    """A dependency is unavailable (connect/timeout/5xx; any non-200 from BPM).

    Retried indefinitely with capped exponential backoff; never counted
    toward a job's attempts.
    """


class PermanentJobError(Exception):
    """This job's input is bad (missing object, corrupt audio, 4xx for it).

    Counted toward attempts; job becomes `failed` after MAX_RETRIES.
    """
