from flask import Flask, jsonify, request, make_response
from flask_cors import CORS
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.desired_capabilities import DesiredCapabilities
import json
import time
import redis
import os
import firebase_admin
from firebase_admin import credentials, firestore

app = Flask(__name__)
CORS(app)

redis_client = redis.Redis.from_url(os.getenv('REDIS_URL', 'redis://localhost:6379'))
cred = credentials.Certificate(json.loads(os.getenv('FIREBASE_CREDENTIALS')))
firebase_admin.initialize_app(cred)
db = firestore.client()

def get_video_url_with_token(embed_url):
    capabilities = DesiredCapabilities.CHROME
    capabilities['goog:loggingPrefs'] = {'performance': 'ALL'}
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--disable-notifications')
    options.add_argument('--disable-popup-blocking')
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options, desired_capabilities=capabilities)
    try:
        driver.get(embed_url)
        wait = WebDriverWait(driver, 15)
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "video")))
        try:
            driver.execute_script("document.querySelector('video').play();")
            time.sleep(3)
        except:
            pass
        logs = driver.get_log('performance')
        video_url = None
        for entry in logs:
            log = json.loads(entry['message'])['message']
            if 'Network.responseReceived' in log['method']:
                try:
                    url = log['params']['response']['url']
                    if ('.m3u8' in url or '.mp4' in url) and ('token=' in url or 'expires=' in url):
                        video_url = url
                        break
                except KeyError:
                    pass
        return video_url or "Error: No URL encontrada"
    finally:
        driver.quit()

@app.route('/api/generate-token', methods=['GET'])
def generate_token():
    embed_url = request.args.get('embed_url')
    if not embed_url:
        return jsonify({'error': 'Falta embed_url'}), 400
    cache_key = f"token:{embed_url}"
    cached_token = redis_client.get(cache_key)
    if cached_token:
        return jsonify({'token_url': cached_token.decode('utf-8')})
    token_url = get_video_url_with_token(embed_url)
    if token_url and token_url != "Error: No URL encontrada":
        redis_client.setex(cache_key, 3600, token_url)
    return jsonify({'token_url': token_url})

@app.route('/vast', methods=['GET'])
def serve_vast():
    ads_type = request.args.get('ads_type', 'propios')
    ad_id = request.args.get('ad_id', 'default')
    if ads_type == 'externos':
        vast_xml = '''<?xml version="1.0" encoding="UTF-8"?>
        <VAST version="3.0">
            <Ad>
                <Wrapper>
                    <VASTAdTagURI><![CDATA[https://pubads.g.doubleclick.net/gampad/ads?sz=640x360&iu=/124319096/external/single_ad_samples&ciu_szs=300x250&impl=s&gdfp_req=1&env=vp&output=vast&unviewed_position_start=1]]></VASTAdTagURI>
                </Wrapper>
            </Ad>
        </VAST>'''
    else:
        ad_configs = {
            'default': {
                'video_url': 'https://streamable.com/o/TU_STREAMABLE_ID',  // Pega tu URL
                'duration': '00:00:15',
                'skip_after': '00:00:05',
                'impression_url': 'https://tu-app.onrender.com/track_impression?ad_id=default',
                'click_url': 'https://tu-sitio.com'
            }
        }
        config = ad_configs.get(ad_id, ad_configs['default'])
        vast_xml = f'''<?xml version="1.0" encoding="UTF-8"?>
        <VAST version="3.0">
            <Ad id="{ad_id}">
                <InLine>
                    <AdSystem>Tu Servicio</AdSystem>
                    <AdTitle>Tu Anuncio</AdTitle>
                    <Impression><![CDATA[{config['impression_url']}]]></Impression>
                    <Creatives>
                        <Creative>
                            <Linear skipoffset="{config['skip_after']}">
                                <Duration>{config['duration']}</Duration>
                                <VideoClicks>
                                    <ClickThrough><![CDATA[{config['click_url']}]]></ClickThrough>
                                </VideoClicks>
                                <MediaFiles>
                                    <MediaFile delivery="progressive" type="video/mp4" width="640" height="360">
                                        <![CDATA[{config['video_url']}]]>
                                    </MediaFile>
                                </MediaFiles>
                            </Linear>
                        </Creative>
                    </Creatives>
                </InLine>
            </Ad>
        </VAST>'''
    response = make_response(vast_xml)
    response.headers['Content-Type'] = 'text/xml'
    return response

@app.route('/track_impression', methods=['GET'])
def track_impression():
    ad_id = request.args.get('ad_id')
    event = request.args.get('event', 'impression')
    db.collection('ad_impressions').add({
        'ad_id': ad_id,
        'event': event,
        'ip': request.remote_addr,
        'timestamp': firestore.SERVER_TIMESTAMP
    })
    return '', 200

@app.route('/api/scraper', methods=['GET'])
def scraper_api():
    query = request.args.get('query', '').lower()
    if not query:
        return jsonify({'error': 'Falta query'}), 400
    videos_ref = db.collection('videos')
    snapshot = videos_ref.where('title_lowercase', '>=', query).where('title_lowercase', '<=', query + '\uf8ff').order_by('timestamp', 'desc').limit(10).get()
    results = []
    for doc in snapshot:
        data = doc.to_dict()
        results.append({
            'title': data['title'],
            'episode': data.get('episode', ''),
            'embed_url': data['embedUrl'],
            'poster': data['poster'],
            'description': data['description']
        })
    return jsonify({'results': results})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
