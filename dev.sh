#!/usr/bin/env bash
# FarFromHome Development Startup Script
set -e

echo "Starting FarFromHome backend on port 8000..."
python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!

echo "Starting Vite frontend dev server on port 5173..."
cd frontend
npm run dev &
FRONTEND_PID=$!

trap "kill $BACKEND_PID $FRONTEND_PID" EXIT
wait
