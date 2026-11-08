# Blog de Sam Marsalis - Site pour Jeu de Rôle Grandeur Nature

Un site de blog simple et élégant conçu pour un jeu de rôle grandeur nature où la personne décédée était Sam Marsalis, leader du Vitry All Stars Brass Band.

## 🎵 Features

- **Clean, modern design** with music-focused aesthetic
- **Three sample blog posts** about vinyl, jazz, and synthesizers
- **Admin panel** with a draft post for game purposes
- **Responsive design** that works on all devices
- **Static files** - easy to deploy anywhere

## 🚀 Quick Start

1. **Install uv** (if not already installed):
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

2. **Download stock images** (optional for demo):
   - Place `vinyl-collection.jpg` in `images/` folder
   - Place `jazz-club.jpg` in `images/` folder  
   - Place `synthesizer.jpg` in `images/` folder

3. **Start the server**:
   ```bash
   uv run python server.py
   ```
   
   Or with a custom port:
   ```bash
   uv run python server.py 8080
   ```

4. **Visit the blog**:
   - Main blog: http://localhost:8000
   - Admin panel: http://localhost:8000/admin/

## 📁 Project Structure

```
├── index.html              # Main blog homepage
├── css/style.css          # All styles
├── images/                # Stock images (add your own)
├── posts/                 # Individual blog posts
│   ├── vinyl-renaissance.html
│   ├── jazz-evolution.html
│   └── synthesizer-magic.html
├── admin/index.html       # Admin panel with draft
├── server.py             # Python HTTP server
├── pyproject.toml        # uv project configuration
└── README.md             # This file
```

## 🔐 Admin Panel

The admin panel contains a pre-written draft post about underground music venues. Players can:
- View and "edit" the draft (changes show visually but don't save)
- Experience what it feels like to be the blogger
- Discover clues within the draft content

**Note**: Basic authentication will be handled in your Kubernetes deployment as requested.

## 🎮 Role-Playing Game Notes

- The draft post contains rich backstory about music venues and communities
- Content designed to feel authentic and personal
- Admin interface looks functional but is demo-only
- Perfect for creating immersive game experiences

## 🛠️ Technical Details

- **Pure HTML/CSS/JavaScript** - no frameworks
- **Python HTTP server** - standard library only, managed with uv
- **uv project** - modern Python package management
- **Static files** - easily deployable
- **Mobile responsive** - works on all devices

## 📷 Adding Images

For best results, add these stock images to the `images/` folder:
- Search "vintage vinyl records collection" → save as `vinyl-collection.jpg`
- Search "jazz club atmosphere night" → save as `jazz-club.jpg`  
- Search "vintage synthesizer electronic" → save as `synthesizer.jpg`

The blog will work without images, but they enhance the visual experience.

---

*Created for an immersive role-playing game experience. Designed to feel like a real music blogger's personal website.* 
