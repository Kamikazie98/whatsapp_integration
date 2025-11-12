import click
import subprocess
import os
from frappe.commands import pass_context

@click.command('start-whatsapp-service')
@pass_context
def start_whatsapp_service(context):
    """Start the NodeJS WhatsApp service with auto-detected Frappe port"""
    
    app_path = os.path.join(context.get_app_path("whatsapp_integration"), "..")
    nodejs_service_path = os.path.join(app_path, "nodejs-whatsapp-service")
    
    if not os.path.exists(nodejs_service_path):
        click.echo("❌ NodeJS service directory not found!")
        return
    
    click.echo("🚀 Starting WhatsApp API Service...")
    
    try:
        # Change to nodejs service directory and run the auto-start script
        os.chdir(nodejs_service_path)
        subprocess.run(["npm", "run", "auto-start"], check=True)
    except subprocess.CalledProcessError as e:
        click.echo(f"❌ Failed to start WhatsApp service: {e}")
    except Exception as e:
        click.echo(f"❌ Error: {e}")

commands = [start_whatsapp_service]
