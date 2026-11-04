"""State-at-checkpoint assertions for deduplicated monitor publications."""
def latest_phase(outputs, thread, cutoff):
    found = None
    for timestamp, snapshot in outputs:
        if timestamp > cutoff:
            break
        if not snapshot['connected']:
            found = 'disconnected'
        else:
            found = next((row['phase'] for row in snapshot['sessions'] if row['id'] == thread), None)
    return found
