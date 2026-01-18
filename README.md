# TripSignal Backend

A FastAPI-based backend foundation for TripSignal.

## Tech Stack

- **FastAPI** - Modern Python web framework
- **PostgreSQL** - Relational database
- **SQLAlchemy 2.x** - ORM
- **Alembic** - Database migrations
- **Docker & Docker Compose** - Containerization
- **Python 3.12** - Runtime

## Project Structure

```
TripSignal/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py              # FastAPI entry point
│   │   ├── core/
│   │   │   ├── __init__.py
│   │   │   ├── config.py        # Environment configuration
│   │   │   └── logging.py       # Logging setup
│   │   ├── db/
│   │   │   ├── __init__.py
│   │   │   ├── base.py          # SQLAlchemy base
│   │   │   └── session.py       # Database session management
│   │   └── api/
│   │       ├── __init__.py
│   │       └── routes/
│   │           ├── __init__.py
│   │           └── health.py    # Health check endpoint
│   └── alembic/                 # Database migrations
├── docker-compose.yml           # Docker Compose configuration
├── Dockerfile                   # Backend container definition
├── requirements.txt             # Python dependencies
├── .env.example                 # Environment variables template
├── Makefile                     # Common commands
└── README.md                    # This file
```

## Local Development

### Prerequisites

- Docker and Docker Compose installed
- (Optional) Make installed (for convenience commands)

### Quick Start

1. **Copy environment file:**
   ```bash
   cp .env.example .env
   ```

2. **Start services:**
   ```bash
   docker compose up --build
   ```
   
   Or using Make:
   ```bash
   make up-build
   ```

3. **Verify the API is running:**
   - Health check: http://localhost:8000/health
   - API docs: http://localhost:8000/docs
   - Root endpoint: http://localhost:8000/

### Common Commands

Using Docker Compose directly:
```bash
# Start services in background
docker compose up -d

# View logs
docker compose logs -f

# Stop services
docker compose down

# Rebuild and restart
docker compose up --build -d
```

Using Make:
```bash
make help          # Show all available commands
make build         # Build Docker images
make up            # Start services
make up-build      # Build and start services
make down          # Stop services
make logs          # View logs from all services
make logs-api      # View API logs only
make logs-db       # View database logs only
make shell         # Open shell in API container
make db-shell      # Open PostgreSQL shell
make clean         # Remove containers, volumes, and images
```

### Database Access

Connect to PostgreSQL:
```bash
# Using Docker Compose
docker compose exec postgres psql -U postgres -d tripsignal

# Or using Make
make db-shell
```

### Database Migrations

Alembic is configured but no migrations are created yet. When you're ready to create models:

```bash
# Create a new migration
docker compose exec api alembic revision --autogenerate -m "Initial migration"

# Apply migrations
docker compose exec api alembic upgrade head
```

## VPS Deployment

### Prerequisites

- Docker and Docker Compose installed on your VPS
- SSH access to your VPS
- (Optional) Domain name configured to point to your VPS

### Deployment Steps

1. **Clone the repository on your VPS:**
   ```bash
   git clone <your-repo-url> TripSignal
   cd TripSignal
   ```

2. **Create environment file:**
   ```bash
   cp .env.example .env
   nano .env  # Edit with your production values
   ```

3. **Update `.env` with production settings:**
   ```env
   POSTGRES_PASSWORD=<strong-password>
   DEBUG=false
   API_PORT=8000
   POSTGRES_PORT=5432
   ```

4. **Build and start services:**
   ```bash
   docker compose up --build -d
   ```

5. **Verify deployment:**
   ```bash
   # Check container status
   docker compose ps

   # Check logs
   docker compose logs -f api

   # Test health endpoint
   curl http://localhost:8000/health
   ```

6. **Set up reverse proxy (optional but recommended):**
   
   If using Nginx:
   ```nginx
   server {
       listen 80;
       server_name your-domain.com;

       location / {
           proxy_pass http://localhost:8000;
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto $scheme;
       }
   }
   ```

### Production Considerations

- **Security:**
  - Use strong passwords in `.env`
  - Don't commit `.env` to version control
  - Consider using Docker secrets for sensitive data
  - Set `DEBUG=false` in production
  - Configure CORS appropriately in `main.py`

- **Database:**
  - Use persistent volumes (already configured in `docker-compose.yml`)
  - Set up regular backups
  - Consider using managed PostgreSQL service for production

- **Monitoring:**
  - Set up log aggregation
  - Monitor container health
  - Set up alerts for service failures

- **Updates:**
  ```bash
   # Pull latest code
   git pull
   
   # Rebuild and restart
   docker compose up --build -d
   
   # Run migrations if needed
   docker compose exec api alembic upgrade head
   ```

## Troubleshooting

### API container won't start

**Problem:** API container exits immediately or shows connection errors.

**Solutions:**
- Check if PostgreSQL container is running: `docker compose ps`
- Verify database credentials in `.env` match `docker-compose.yml`
- Check logs: `docker compose logs api`
- Ensure PostgreSQL health check passes: `docker compose logs postgres`

### Database connection errors

**Problem:** "Connection refused" or "authentication failed" errors.

**Solutions:**
- Verify `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB` match in both `.env` and `docker-compose.yml`
- Check PostgreSQL logs: `docker compose logs postgres`
- Ensure PostgreSQL container is healthy: `docker compose ps`
- Try connecting manually: `docker compose exec postgres psql -U postgres -d tripsignal`

### Port already in use

**Problem:** Error about port 8000 or 5432 already being in use.

**Solutions:**
- Change ports in `.env`:
  ```env
  API_PORT=8001
  POSTGRES_PORT=5433
  ```
- Or stop the conflicting service:
  ```bash
  # Find process using port 8000
  lsof -i :8000
  # Kill the process (replace PID)
  kill -9 <PID>
  ```

### Container build fails

**Problem:** Docker build errors during `docker compose up --build`.

**Solutions:**
- Check Docker daemon is running: `docker ps`
- Verify `requirements.txt` is correct
- Clear Docker cache: `docker compose build --no-cache`
- Check disk space: `df -h`

### Health check fails

**Problem:** `/health` endpoint returns error or "degraded" status.

**Solutions:**
- Check database connection: `docker compose exec api python -c "from app.db.session import check_db_connection; print(check_db_connection())"`
- Verify database is accessible: `docker compose exec postgres pg_isready -U postgres`
- Review API logs: `docker compose logs api`

## API Endpoints

### Health Check
- **GET** `/health` - Returns API and database status
  ```json
  {
    "status": "ok",
    "database": "connected"
  }
  ```

### API Documentation
- **GET** `/docs` - Interactive API documentation (Swagger UI)
- **GET** `/redoc` - Alternative API documentation (ReDoc)

## Environment Variables

See `.env.example` for all available environment variables. Key variables:

- `POSTGRES_USER` - PostgreSQL username (default: postgres)
- `POSTGRES_PASSWORD` - PostgreSQL password (default: postgres)
- `POSTGRES_DB` - Database name (default: tripsignal)
- `POSTGRES_HOST` - Database host (default: postgres)
- `POSTGRES_PORT` - Database port (default: 5432)
- `API_PORT` - API server port (default: 8000)
- `DEBUG` - Enable debug mode (default: false)

## Next Steps

- Create database models in `app/db/models/`
- Set up authentication if needed
- Add more API endpoints
- Configure production logging
- Set up CI/CD pipeline
- Add tests

## License

[Your License Here]
