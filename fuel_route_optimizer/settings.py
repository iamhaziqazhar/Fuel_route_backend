import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / '.env')

SECRET_KEY = os.getenv(
    'DJANGO_SECRET_KEY',
    'django-insecure-l4f1thcvgjb0=-xa1nn0%28kht7)4twvp(xy*&mtg97do8!(u8',
)

DEBUG = os.getenv('DJANGO_DEBUG', 'True').lower() == 'true'

ALLOWED_HOSTS = [h.strip() for h in os.getenv('DJANGO_ALLOWED_HOSTS', '').split(',') if h.strip()]

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'api',
]

MIDDLEWARE = [
    'django.middleware.gzip.GZipMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'fuel_route_optimizer.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'fuel_route_optimizer.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'

MAP_ROUTING_API_KEY = os.getenv('MAP_ROUTING_API_KEY', '')
MAP_ROUTING_API_BASE_URL = os.getenv(
    'MAP_ROUTING_API_BASE_URL', 'https://api.openrouteservice.org'
)

GEOCODING_API_KEY = os.getenv('GEOCODING_API_KEY', '')
GEOCODING_API_BASE_URL = os.getenv(
    'GEOCODING_API_BASE_URL', 'https://api.opencagedata.com/geocode/v1'
)

FUEL_PRICE_API_KEY = os.getenv('FUEL_PRICE_API_KEY', '')
FUEL_PRICE_API_BASE_URL = os.getenv(
    'FUEL_PRICE_API_BASE_URL', 'https://api.collectapi.com/gasPrice'
)

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'fuel-route-optimizer-locmem',
        'TIMEOUT': 86400,
        'OPTIONS': {
            'MAX_ENTRIES': 5000,
        },
    }
}

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.SessionAuthentication',
        'rest_framework.authentication.BasicAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticatedOrReadOnly',
    ],
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
        'rest_framework.renderers.BrowsableAPIRenderer',
    ],
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '10/min',
        'user': '60/min',
    },
}

PREWARM_ROUTES: list[tuple[str, str]] = [
    ('Los Angeles, CA', 'New York, NY'),
    ('Dallas, TX', 'Chicago, IL'),
    ('Atlanta, GA', 'Miami, FL'),
    ('Seattle, WA', 'San Francisco, CA'),
    ('Houston, TX', 'Phoenix, AZ'),
    ('Denver, CO', 'Kansas City, MO'),
    ('Boston, MA', 'Washington, DC'),
    ('Minneapolis, MN', 'Chicago, IL'),
    ('Portland, OR', 'Salt Lake City, UT'),
    ('Nashville, TN', 'Atlanta, GA'),
]
