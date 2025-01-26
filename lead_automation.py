import requests
import pandas as pd
from datetime import datetime
import time
import logging
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import openai
from bs4 import BeautifulSoup
import json
import sqlite3
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from pydantic import BaseModel
from typing import List, Dict, Optional
from dotenv import load_dotenv
import os
import asyncio
import aiohttp
from contextlib import asynccontextmanager

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('lead_automation.log'),
        logging.StreamHandler()
    ]
)

class Config:
    OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
    CRUNCHBASE_API_KEY = os.getenv('CRUNCHBASE_API_KEY')
    
    @classmethod
    def validate_config(cls):
        """Validate that all required environment variables are set"""
        missing_vars = []
        if not cls.OPENAI_API_KEY:
            missing_vars.append('OPENAI_API_KEY')
        if not cls.CRUNCHBASE_API_KEY:
            missing_vars.append('CRUNCHBASE_API_KEY')
            
        if missing_vars:
            raise EnvironmentError(f"Missing required environment variables: {', '.join(missing_vars)}")

# Configure API clients
openai.api_key = Config.OPENAI_API_KEY
CRUNCHBASE_API_BASE = "https://api.crunchbase.com/api/v4"

class LeadAutomation:
    def __init__(self, db_path: str = 'leads.db'):
        Config.validate_config()
        self.db_path = db_path
        self.setup_database()
        self.stats = {
            'leads_generated': 0,
            'leads_enriched': 0,
            'errors': 0
        }
        self.session = None

    async def init_session(self):
        """Initialize aiohttp session"""
        if not self.session:
            self.session = aiohttp.ClientSession()
    
    async def close_session(self):
        """Close aiohttp session"""
        if self.session:
            await self.session.close()
            self.session = None

# Define async automation cycle function
async def run_automation_cycle():
    """Run a complete automation cycle"""
    automation = LeadAutomation()
    await automation.init_session()
    
    try:
        categories = ['software', 'ecommerce', 'healthcare', 'fintech', 'ai', 'cybersecurity']
        for category in categories:
            leads = await automation.scrape_crunchbase(category)
            
            for lead in leads:
                enriched_data = await automation.enrich_lead(lead)
                await automation.store_lead(lead, enriched_data)
                
            logging.info(f"Completed processing {len(leads)} leads for category: {category}")
    finally:
        await automation.close_session()

# Define FastAPI app and lifespan
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    automation_task = None
    
    async def run_scheduled_automation():
        while True:
            await run_automation_cycle()
            await asyncio.sleep(4 * 60 * 60)  # 4 hours
    
    try:
        automation_task = asyncio.create_task(run_scheduled_automation())
        yield
    finally:
        # Shutdown
        if automation_task:
            automation_task.cancel()
            try:
                await automation_task
            except asyncio.CancelledError:
                pass

# Create FastAPI app with lifespan
app = FastAPI(
    title="Lead Automation Dashboard",
    description="API for managing and monitoring lead generation automation",
    version="1.0.0",
    lifespan=lifespan
)

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Define routes
@app.get("/", response_class=HTMLResponse)
async def root():
    """Root endpoint with HTML dashboard"""
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Lead Automation Dashboard</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                margin: 40px;
                line-height: 1.6;
            }
            .container {
                max-width: 800px;
                margin: 0 auto;
            }
            .header {
                background-color: #f4f4f4;
                padding: 20px;
                border-radius: 5px;
                margin-bottom: 20px;
            }
            .section {
                margin-bottom: 30px;
            }
            .button {
                display: inline-block;
                padding: 10px 20px;
                background-color: #007bff;
                color: white;
                text-decoration: none;
                border-radius: 5px;
                margin-right: 10px;
            }
            .button:hover {
                background-color: #0056b3;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Lead Automation Dashboard</h1>
                <p>Monitor and manage your automated lead generation system</p>
            </div>
            
            <div class="section">
                <h2>Quick Links</h2>
                <a href="/stats" class="button">View Statistics</a>
                <a href="/leads" class="button">View Leads</a>
                <a href="/docs" class="button">API Documentation</a>
            </div>
            
            <div class="section">
                <h2>System Status</h2>
                <p>Automation running every 4 hours:</p>
                <ul>
                    <li>Scraping leads from Crunchbase</li>
                    <li>Enriching with SEO analysis</li>
                    <li>Generating GPT insights</li>
                    <li>Storing in database</li>
                </ul>
            </div>
        </div>
    </body>
    </html>
    """
    return html_content

@app.get("/stats")
async def get_stats():
    """Get current automation statistics"""
    try:
        automation = LeadAutomation()
        return JSONResponse(content=automation.stats)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/leads")
async def get_leads(limit: int = 100, offset: int = 0):
    """Get latest leads with enrichment data"""
    try:
        conn = sqlite3.connect('leads.db')
        df = pd.read_sql_query('''
            SELECT l.*, e.seo_score, e.identified_problems, e.opportunities
            FROM leads l
            LEFT JOIN enrichment_data e ON l.id = e.lead_id
            ORDER BY l.created_at DESC
            LIMIT ? OFFSET ?
        ''', conn, params=[limit, offset])
        conn.close()
        return JSONResponse(content=df.to_dict('records'))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    """Check system health"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": "1.0.0"
    }


async def test_crunchbase_api():
    url = f"https://api.crunchbase.com/api/v4/entities/organizations/example?user_key={os.getenv('CRUNCHBASE_API_KEY')}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            print("Status code:", response.status)  # Check for 200 or error
            data = await response.json()
            print(data)

asyncio.run(test_crunchbase_api())


# Main entry point
if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
