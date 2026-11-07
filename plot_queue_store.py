"""Persistent list of plots set up ahead of time, to be run later.

A queued plot here is just saved settings (file, title, layer, tool,
material); nothing happens on the machine until someone loads it and presses Plot.
"""
import json
import os
import threading
import time
import uuid

QUEUE_FIELDS = ('file', 'title', 'layer', 'tool', 'media')


class PlotQueueStore:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()

    def _read(self):
        if not os.path.exists(self.path):
            return []

        try:
            with open(self.path, encoding='utf-8') as queue_file:
                entries = json.load(queue_file)
        except (OSError, json.JSONDecodeError) as error:
            print(f"[WARN] Could not read plot queue {self.path}: {error}")
            return []

        return entries if isinstance(entries, list) else []

    def _write(self, entries):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        temp_path = f"{self.path}.tmp"
        with open(temp_path, 'w', encoding='utf-8') as queue_file:
            json.dump(entries, queue_file, ensure_ascii=True, indent=2)
        os.replace(temp_path, self.path)

    def list(self):
        """Return queued plots, oldest first (the order they should be plotted)."""
        with self.lock:
            return self._read()

    def add(self, file, title='', layer='', tool='None', media='None'):
        entry = {
            'id': uuid.uuid4().hex,
            'file': file,
            'title': title,
            'layer': str(layer) if layer not in (None, 'all') else '',
            'tool': tool or 'None',
            'media': media or 'None',
            'added_at': int(time.time()),
        }

        with self.lock:
            entries = self._read()
            entries.append(entry)
            self._write(entries)

        return entry

    def remove(self, entry_id):
        """Delete an entry. Returns True if it existed."""
        with self.lock:
            entries = self._read()
            remaining = [entry for entry in entries if entry.get('id') != entry_id]
            if len(remaining) == len(entries):
                return False
            self._write(remaining)
            return True
