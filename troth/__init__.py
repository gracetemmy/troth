"""Troth — a pledged word.

A sales/client copilot that remembers what was actually promised, and
refuses to repeat a promise it has not verified.

Every persistence call in this package goes to Sibyl Memory. There is no
local cache, no in-memory fallback, and no mock. Delete the Sibyl
database and Troth has nothing to answer with.
"""

__version__ = "0.1.0"
