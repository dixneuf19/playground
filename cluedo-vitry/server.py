#!/usr/bin/env python3
"""
Simple HTTP server for the music blog with login form authentication for admin panel.
Run with: uv run python server.py
Then visit: http://localhost:8000
Admin panel: http://localhost:8000/admin/ (requires password via login form)

Environment variables:
- ADMIN_PASSWORD: Set the admin password (required for admin access)
"""

import http.server
import socketserver
import os
import json
import hashlib
import time
import mimetypes
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timedelta

class BlogHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    """Custom handler that serves the blog files and handles admin routes with form authentication."""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=os.getcwd(), **kwargs)
    
    def do_GET(self):
        """Handle GET requests with authentication for admin routes."""
        parsed_path = urlparse(self.path)
        
        # Check if this is an admin route
        if parsed_path.path.startswith('/admin'):
            if parsed_path.path == '/admin/login':
                self.serve_login_page()
                return
            elif parsed_path.path == '/admin/logout':
                self.handle_logout()
                return
            elif not self.is_authenticated():
                self.redirect_to_login()
                return

            
            self.log_message("Admin panel accessed: %s", self.path)
        
        # Handle root directory - serve index.html
        if parsed_path.path == '/':
            self.path = '/index.html'
        
        # Serve the file
        return super().do_GET()
    
    def do_POST(self):
        """Handle POST requests for login form."""
        parsed_path = urlparse(self.path)
        
        if parsed_path.path == '/admin/login':
            self.handle_login()
        else:
            self.send_error(405, "Method not allowed")
    
    def get_file_type_category(self, path):
        """Categorize file type for caching strategy."""
        mime_type, _ = mimetypes.guess_type(path)
        
        if not mime_type:
            return 'other'
        
        # Images: Long cache (30 days)
        if mime_type.startswith('image/'):
            return 'image'
        
        # CSS/JS: Medium cache (7 days)
        if mime_type in ['text/css', 'application/javascript', 'text/javascript']:
            return 'static'
        
        # HTML: Short cache (1 hour)
        if mime_type == 'text/html':
            return 'html'
        
        # Fonts: Long cache (30 days)
        if mime_type.startswith('font/') or path.endswith(('.woff', '.woff2', '.ttf', '.eot')):
            return 'font'
        
        return 'other'
    
    def is_authenticated(self):
        """For game purposes: always require re-authentication."""
        return False
    

    
    def handle_login(self):
        """Handle login form submission."""
        admin_password = os.environ.get('ADMIN_PASSWORD')
        
        if not admin_password:
            self.send_error(500, "Admin access requires ADMIN_PASSWORD environment variable to be set")
            return
        
        # Read form data
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length).decode('utf-8')
        form_data = parse_qs(post_data)
        
        submitted_password = form_data.get('password', [''])[0]
        
        if submitted_password == admin_password:
            # Serve the admin page directly (no redirect, no cookie)
            self.serve_admin_file()
        else:
            # Redirect back to login with error
            self.send_response(302)
            self.send_header('Location', '/admin/login?error=1')
            self.end_headers()
    
    def handle_logout(self):
        """Handle logout request (redirect to home)."""
        self.send_response(302)
        self.send_header('Location', '/')
        self.end_headers()
    
    def redirect_to_login(self):
        """Redirect to login page."""
        self.send_response(302)
        self.send_header('Location', '/admin/login')
        self.end_headers()
    
    def serve_login_page(self):
        """Serve the login form page."""
        parsed_path = urlparse(self.path)
        query_params = parse_qs(parsed_path.query)
        show_error = 'error' in query_params
        
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
        
        html_content = f"""
        <!DOCTYPE html>
        <html lang="fr">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>🔐 Connexion Admin - Blog de Sam Marsalis</title>
            <style>
                body {{
                    font-family: 'Georgia', serif;
                    background: linear-gradient(135deg, #1a1a2e, #16213e);
                    color: #f5f5f5;
                    margin: 0;
                    padding: 0;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    min-height: 100vh;
                }}
                .login-container {{
                    background: rgba(255, 255, 255, 0.1);
                    backdrop-filter: blur(10px);
                    border-radius: 15px;
                    padding: 40px;
                    text-align: center;
                    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
                    max-width: 400px;
                    width: 90%;
                }}
                h1 {{
                    color: #f4d03f;
                    margin-bottom: 20px;
                    font-size: 1.8em;
                }}
                .trumpet-emoji {{
                    font-size: 3em;
                    margin-bottom: 20px;
                }}
                .form-group {{
                    margin-bottom: 20px;
                    text-align: left;
                }}
                label {{
                    display: block;
                    margin-bottom: 8px;
                    color: #f5f5f5;
                    font-weight: bold;
                }}
                input[type="password"] {{
                    width: 100%;
                    padding: 12px;
                    border: 2px solid #85c1e9;
                    border-radius: 8px;
                    background: rgba(255, 255, 255, 0.1);
                    color: #f5f5f5;
                    font-size: 16px;
                    box-sizing: border-box;
                }}
                input[type="password"]:focus {{
                    outline: none;
                    border-color: #f4d03f;
                    background: rgba(255, 255, 255, 0.2);
                }}
                .login-btn {{
                    background: linear-gradient(45deg, #f4d03f, #f7dc6f);
                    color: #1a1a2e;
                    border: none;
                    padding: 12px 30px;
                    border-radius: 8px;
                    font-size: 16px;
                    font-weight: bold;
                    cursor: pointer;
                    transition: all 0.3s ease;
                    width: 100%;
                }}
                .login-btn:hover {{
                    transform: translateY(-2px);
                    box-shadow: 0 4px 15px rgba(244, 208, 63, 0.4);
                }}
                .error-message {{
                    background: rgba(231, 76, 60, 0.2);
                    border: 1px solid #e74c3c;
                    color: #f5f5f5;
                    padding: 10px;
                    border-radius: 5px;
                    margin-bottom: 20px;
                }}
                .back-link {{
                    color: #85c1e9;
                    text-decoration: none;
                    padding: 10px 20px;
                    border: 2px solid #85c1e9;
                    border-radius: 5px;
                    transition: all 0.3s ease;
                    display: inline-block;
                    margin-top: 20px;
                }}
                .back-link:hover {{
                    background-color: #85c1e9;
                    color: #1a1a2e;
                }}
            </style>
        </head>
        <body>
            <div class="login-container">
                <div class="trumpet-emoji">🎺</div>
                <h1>🔐 Administration</h1>
                <p>Accès au panneau d'administration du blog de Sam Marsalis</p>
                
                {'<div class="error-message">❌ Mot de passe incorrect. Veuillez réessayer.</div>' if show_error else ''}
                
                <form method="POST" action="/admin/login">
                    <div class="form-group">
                        <label for="password">Mot de passe :</label>
                        <input type="password" id="password" name="password" required autofocus>
                    </div>
                    <button type="submit" class="login-btn">🔓 Se connecter</button>
                </form>
                
                <a href="/" class="back-link">← Retour au blog</a>
            </div>
        </body>
        </html>
        """
        self.wfile.write(html_content.encode('utf-8'))
    
    def serve_admin_file(self):
        """Serve the admin/index.html file directly."""
        try:
            with open('admin/index.html', 'r', encoding='utf-8') as f:
                content = f.read()
            
            self.send_response(200)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Expires', '0')
            self.end_headers()
            self.wfile.write(content.encode('utf-8'))
        except FileNotFoundError:
            self.send_error(404, "Admin page not found")
        except Exception as e:
            self.send_error(500, f"Error serving admin page: {e}")
    
    def end_headers(self):
        """Add intelligent caching headers based on file type."""
        parsed_path = urlparse(self.path)
        file_category = self.get_file_type_category(parsed_path.path)
        
        # Admin routes: No cache
        if parsed_path.path.startswith('/admin'):
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Expires', '0')
        
        # Index.html files: No cache (for easy updates)
        elif parsed_path.path.endswith('index.html') or parsed_path.path == '/' or parsed_path.path == '/index.html':
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Expires', '0')
        
        # Images: Long cache (30 days)
        elif file_category == 'image':
            max_age = 30 * 24 * 60 * 60  # 30 days in seconds
            expires = datetime.utcnow() + timedelta(days=30)
            self.send_header('Cache-Control', f'public, max-age={max_age}, immutable')
            self.send_header('Expires', expires.strftime('%a, %d %b %Y %H:%M:%S GMT'))
            
            # Add ETag for better caching
            try:
                file_path = self.translate_path(self.path)
                if os.path.exists(file_path):
                    stat = os.stat(file_path)
                    etag = f'"{stat.st_mtime}-{stat.st_size}"'
                    self.send_header('ETag', etag)
                    self.send_header('Last-Modified', time.strftime('%a, %d %b %Y %H:%M:%S GMT', time.gmtime(stat.st_mtime)))
            except:
                pass
        
        # CSS/JS: Medium cache (7 days)
        elif file_category == 'static':
            max_age = 7 * 24 * 60 * 60  # 7 days in seconds
            expires = datetime.utcnow() + timedelta(days=7)
            self.send_header('Cache-Control', f'public, max-age={max_age}')
            self.send_header('Expires', expires.strftime('%a, %d %b %Y %H:%M:%S GMT'))
        
        # Fonts: Long cache (30 days)
        elif file_category == 'font':
            max_age = 30 * 24 * 60 * 60  # 30 days in seconds
            expires = datetime.utcnow() + timedelta(days=30)
            self.send_header('Cache-Control', f'public, max-age={max_age}, immutable')
            self.send_header('Expires', expires.strftime('%a, %d %b %Y %H:%M:%S GMT'))
        
        # HTML pages (except index.html): Short cache (1 hour)
        elif file_category == 'html':
            max_age = 60 * 60  # 1 hour in seconds
            expires = datetime.utcnow() + timedelta(hours=1)
            self.send_header('Cache-Control', f'public, max-age={max_age}')
            self.send_header('Expires', expires.strftime('%a, %d %b %Y %H:%M:%S GMT'))
        
        # Other files: Short cache (1 hour)
        else:
            max_age = 60 * 60  # 1 hour in seconds
            expires = datetime.utcnow() + timedelta(hours=1)
            self.send_header('Cache-Control', f'public, max-age={max_age}')
            self.send_header('Expires', expires.strftime('%a, %d %b %Y %H:%M:%S GMT'))
        
        # Add compression hint for all text files
        if file_category in ['html', 'static']:
            self.send_header('Vary', 'Accept-Encoding')
        
        super().end_headers()

def run_server(port=8000):
    """Start the HTTP server."""
    handler = BlogHTTPRequestHandler
    
    # Check if admin password is configured
    admin_password = os.environ.get('ADMIN_PASSWORD')
    
    try:
        with socketserver.TCPServer(("", port), handler) as httpd:
            print(f"🎺 Blog de Sam Marsalis - Serveur")
            print(f"🌐 Disponible sur: http://localhost:{port}")
            print(f"🔐 Admin: http://localhost:{port}/admin/")
            print(f"🚀 Cache optimisé pour Raspberry Pi")
            
            if admin_password:
                print(f"✅ Authentification admin: activée (mode jeu)")
                print(f"🔗 Connexion: http://localhost:{port}/admin/login")
                print(f"🎮 Mode: Reconnexion requise à chaque accès")
            else:
                print(f"⚠️  Authentification admin: DÉSACTIVÉE (définir ADMIN_PASSWORD)")
            
            print(f"📁 Dossier: {os.getcwd()}")
            print(f"⏹️  Arrêt: Ctrl+C")
            print("-" * 60)
            
            httpd.serve_forever()
            
    except KeyboardInterrupt:
        print("\n👋 Serveur arrêté")
    except OSError as e:
        if e.errno == 48:  # Address already in use
            print(f"❌ Port {port} déjà utilisé. Essayez:")
            print(f"   python server.py {port + 1}")
        else:
            print(f"❌ Erreur serveur: {e}")

def main():
    """Main entry point for the blog server."""
    import sys
    
    # Allow custom port via command line argument
    port = 8000
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            print("Port invalide. Utilisation du port 8000.")
    
    run_server(port)

if __name__ == "__main__":
    main() 
