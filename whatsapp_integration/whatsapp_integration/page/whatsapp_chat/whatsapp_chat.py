import frappe

def get_context(context):
    """No server-side context needed; placeholder to keep Page export happy."""
    context.no_cache = 1
