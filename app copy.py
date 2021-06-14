import os
import configuration
import re
import requests

from datetime import date
from dateutil.relativedelta import relativedelta
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask import flash, g, redirect, render_template, request, url_for, session
from itsdangerous import TimedJSONWebSignatureSerializer as Serializer
from werkzeug.security import generate_password_hash, check_password_hash
from api import multiSearch, lookupById, lookupTvById, lookupReleaseDate, lookupRelatedTv, lookupRelatedMovies, lookupUpcoming, lookupPopular, lookupPersonMovies, lookupPersonProfile, lookupGenre, lookupTvGenre
from utilities import login_required, check_confirmed, generate_confirmation_token, confirm_token
from emailer import send_release_mail, send_reset_mail, send_confirmation_email
from datetime import datetime

# define our flask app
app = Flask(__name__)

# setting up config

app.config.update(
    SECRET_KEY = configuration.SECRET_KEY_STORAGE,
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax'
)
# used to switch DB
ENV = 'launch'
if ENV == 'dev':
    app.config['SQLALCHEMY_DATABASE_URI'] = configuration.DATABASE_URL
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
# gets rid of annoying error message
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# init db
db = SQLAlchemy(app)

# our database models

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True)
    password = db.Column(db.String(255))
    confirmed = db.Column(db.Boolean, default=False)
    def get_reset_token(self, expires_sec=1800):
        s = Serializer(app.config['SECRET_KEY'], expires_sec)
        return s.dumps({'user_id': self.id}).decode('utf8')

    @staticmethod
    def verify_reset_token(token):
        s = Serializer(app.config['SECRET_KEY'])
        try:
            user_id = s.loads(token)['user_id']
        except:
            return None
        return User.query.get(user_id)

    def __init__(self, username, password, confirmed):
        self.username = username
        self.password = password
        self.confirmed = confirmed


class Follows(db.Model):
    __tablename__ = 'follows'
    follow_id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer)
    movie_id = db.Column(db.Integer)
    movie_title = db.Column(db.String)
    movie_date = db.Column(db.Text())
    movie_type = db.Column(db.String)

    def __init__(self, user_id, movie_id, movie_title, movie_date, movie_type):
        self.user_id = user_id
        self.movie_id = movie_id
        self.movie_title = movie_title
        self.movie_date = movie_date
        self.movie_type = movie_type

# main page route

@app.route('/', methods=('GET', 'POST'))
def index():
    # acquire the movie title from form and pass it to the results page
    if request.method == 'POST':
        query = request.form['movie_title']
        # parse input to make sure its not empty and that the search will return some results
        if query == '':
            error = 'Please provide a movie title'
        # parse input to make sure it has some results
        elif multiSearch(query) == []:
            error = 'Please refine your search query to be more specific'
        # if valid and results are found - redirect to results page - passing the query
        else:
            # New multisearch function implementation
            return redirect(url_for('results', query=query))
        flash(error, 'warning')
    # get request will display index
    return render_template('dashboard/index.html', home=True)

# results route - takes one argument which is the query string

@app.route('/results', methods=('GET', 'POST'))
def results():
    page_chunk = 2
    query = request.args.get('query')
    try:
        adult = session['adult']
    except Exception as e:
        adult = 'false'
        pass
    # New multisearch function implementation
    pageCount = requests.get(
        f"https://api.themoviedb.org/3/search/multi?api_key={configuration.API_KEY_STORAGE}&language=en-US&query={query}&include_adult={adult}&region=US").json()["total_pages"] / page_chunk
    page = request.args.get('page')
    if page is None:
        page = 1
    else:
        page = int(page)
    # use our api to get results based on query string
    searchQuery = multiSearch(query, page, adult)
    # render template with results
    if request.method == 'GET':
        if searchQuery == []:
            error = 'Please refine your search query to be more specific'
        else:
            return render_template('search/results.html', results=searchQuery, page=page, pageCount=pageCount, query=query)
        flash(error, 'warning')
        return redirect(url_for('index'))
    # if user clicks the follow button
    elif request.method == 'POST':
        # get the movie id from the form and look it up in our api
        id = request.form['movie_id']
        movie = lookupById(id)[0]
        # set error to none
        error = None
        # check to make sure user is not already following
        if db.session.query(Follows).filter(Follows.user_id == session['user_id'], Follows.movie_id == movie['id']).count() > 0:
            # set error message appropriately
            error = 'You are already following {}!'.format(movie['name'])
        # if not following then insert the follow into the database
        else:
            insert_follow = Follows(
                session['user_id'], movie['id'], movie['name'], movie['release_small'])
            db.session.add(insert_follow)
            db.session.commit()
            # flask success message and re-render template
            flash('Now following {}!'.format(movie['name']), 'success')
            return render_template('search/results.html', results=searchQuery, page=page, pageCount=pageCount, query=query)
        # flash error message
        flash(error, 'warning')
    return render_template('search/results.html', results=searchQuery, page=page, pageCount=pageCount, query=query)

# details route to show movie info and imdb link / takes one arguement (id of the movie)
@app.route('/details/<string:mediaType>/<int:id>', methods=('GET', 'POST'))
def details(id, mediaType):
    # For TV
    if mediaType == 'tv':
        movie = lookupTvById(id)
        related = lookupRelatedTv(id)
        bg = movie[0]['backdrop']
    # For Movies
    else:
        movie = lookupById(id)
        related = lookupRelatedMovies(id)
        bg = movie[0]['backdrop']
    # if get - then get api information and pass that to the template
    if request.method == 'GET':
        if movie[0]['release_full'] == 'N/A':
            release_year = 'N/A'
            release = 'N/A'
        else:
            date_obj = datetime.strptime(movie[0]['release_full'], '%B %d, %Y')
            release_year = date_obj.strftime('%Y')
            release = movie[0]['release_full']
        return render_template('search/details.html', details=movie, release=release, year=release_year, related=related, mediaType=mediaType, bg = bg, title=movie[0]['name'])
    # else if they click the follow button
    elif request.method == 'POST':
        release_date = None
        # Adding functionality to follow either theatre / digital release OR both
        theatre = request.form.get('theatrical')
        digital = request.form.get('digital')
        # Lookup movie in database
        movie = lookupById(id)[0]
        # Get release info based on users input
        # For BOTH types of releases
        if theatre is not None and digital is not None:
            release_obj = lookupReleaseDate(id)
            release_date_list = [release_obj['digital']['small'], release_obj['theatre']['small']]
        # For digital releases only
        elif digital == 'digital' and theatre is None:
            release_date = lookupReleaseDate(id)
            release_date = release_date['digital']['small']
            movie_type = 'digital'
        # For theatrical releases only
        elif theatre == 'theatre' and digital is None:
            release_date = lookupReleaseDate(id)
            release_date = release_date['theatre']['small']
            movie_type = 'theatrical'

        error = None

        # check to make sure user is not already following
        if db.session.query(Follows).filter(Follows.user_id == g.user.id, Follows.movie_id == movie['id']).count() > 0:
            error = 'You are already following {}!'.format(movie['name'])
        else:
            # if not following then insert the follow into the database
            insert_follow = Follows(
                g.user.id, movie['id'], movie['name'], movie['release_small'])
            db.session.add(insert_follow)
            db.session.commit()
            # success message and reload new follows page
            flash('Now following {}!'.format(movie['name']), 'success')
            return redirect(url_for('follows'))
        # flash any error message
    flash(error, 'warning')
    return redirect(url_for('follows'))

@app.route('/upcoming', methods=('GET', 'POST'))
def upcoming():
    # Get three months ahead date - to cap our upcoming end date
    three_months_ahead = date.today() + relativedelta(months=+3)
    # Set page chunk size which will grab X amount of pages from the api_key
    # And also be used to divide our total page count by to get an accurate page total
    page_chunk = 2
    # Get today's date for the start date of our query
    today = date.today()
    # Get sort by preference --- default is popularity descending
    sort_by = request.args.get('sort_by') or 'popularity.desc'
    # Get the page count of available results from the API and store it in a variable
    pageCount = requests.get(
              f"https://api.themoviedb.org/3/discover/movie?api_key={configuration.API_KEY_STORAGE}&language=en-US&sort_by={sort_by}&include_adult=true&include_video=false&primary_release_date.gte={today}&primary_release_date.lte={three_months_ahead}").json()["total_pages"] / page_chunk
    # Get current page
    page = request.args.get('page')
    # Set page value to 1 or if an argument is passed in - that value
    if page is None:
        page = 1
    else:
        page = int(page)
    # Query our api
    results = lookupUpcoming(page, sort_by)
    # render template with results
    if request.method == 'GET':
        if results == []:
            return redirect(url_for('index'))
        return render_template('search/upcoming.html', results=results, pageCount=pageCount, page=page, sort_by=sort_by)
    # if user clicks the follow button
    elif request.method == 'POST':
        # get the movie id from the form and look it up in our api
        id = request.form['movie_id']
        movie = lookupById(id)[0]
        # set error to none
        error = None
        # check to make sure user is not already following
        if db.session.query(Follows).filter(Follows.user_id == session['user_id'], Follows.movie_id == movie['id']).count() > 0:
            # set error message appropriately
            error = 'You are already following {}!'.format(movie['name'])
        # if not following then insert the follow into the database
        else:
            insert_follow = Follows(
                session['user_id'], movie['id'], movie['name'], movie['release_small'])
            db.session.add(insert_follow)
            db.session.commit()
            # flask success message and re-render template
            flash('Now following {}!'.format(movie['name']), 'success')
            return render_template('search/upcoming.html', results=results, pageCount=pageCount, page=page, sort_by=sort_by)
        # flash error message
        flash(error, 'warning')
    return render_template('search/upcoming.html', results=results, pageCount=pageCount, page=page, sort_by=sort_by)


@app.route('/popular', methods=('GET', 'POST'))
def popular():
    # Set page chunk value
    page_chunk = 2
    # Calculate page count
    pageCount = requests.get(
        f"https://api.themoviedb.org/3/movie/popular?api_key={configuration.API_KEY_STORAGE}&language=en-US&region=US").json()["total_pages"] / page_chunk
    # Get current page
    page = request.args.get('page')
    # Set page to 1 or convert page to an int
    if page is None:
        page = 1
    else:
        page = int(page)
    # Query the api
    popularMovies = lookupPopular(page)
    # Sort api by popularity
    # ------------------------ TODO ----------------------------------------------
    # ADD SORTING FUNCTIONALITY
    def sort(e):
        return e['popularity']
    popularMovies.sort(reverse=True, key=sort)
    # render template with results
    if request.method == 'GET':
        if popularMovies == []:
            return redirect(url_for('index'))
        return render_template('search/popular.html', results=popularMovies, page=page, pageCount=pageCount)
    # if user clicks the follow button
    elif request.method == 'POST':
        # get the movie id from the form and look it up in our api
        id = request.form['movie_id']
        movie = lookupById(id)[0]
        # set error to none
        error = None
        # check to make sure user is not already following
        if db.session.query(Follows).filter(Follows.user_id == session['user_id'], Follows.movie_id == movie['id']).count() > 0:
            # set error message appropriately
            error = 'You are already following {}!'.format(movie['name'])
        # if not following then insert the follow into the database
        else:
            insert_follow = Follows(
                session['user_id'], movie['id'], movie['name'], movie['release_small'])
            db.session.add(insert_follow)
            db.session.commit()
            # flask success message and re-render template
            flash('Now following {}!'.format(movie['name']), 'success')
            return render_template('search/popular.html', results=popularMovies, page=page, pageCount=pageCount)
        # flash error message
        flash(error, 'warning')
    return render_template('search/popular.html', results=popularMovies, page=page, pageCount=pageCount)

@app.route('/people/filmography/<int:id>/<string:name>', methods=('GET', 'POST'))
def peoplemovies(id, name):
    # Set page count and page to 1 - only one page is returned
    pageCount = 1
    page = 1
    # use our api to get results based on query string
    searchQuery = lookupPersonMovies(id)
    # render template with results
    if request.method == 'GET':
        if searchQuery == []:
            error = 'Please refine your search query to be more specific'
        else:
            return render_template('search/results.html', results=searchQuery, page=page, pageCount=pageCount, query=id, name=name)
        flash(error, 'warning')
        return redirect(url_for('index'))
    # if user clicks the follow button
    elif request.method == 'POST':
        # get the movie id from the form and look it up in our api
        id = request.form['movie_id']
        movie = lookupById(id)[0]
        # set error to none
        error = None
        # check to make sure user is not already following
        if db.session.query(Follows).filter(Follows.user_id == session['user_id'], Follows.movie_id == movie['id']).count() > 0:
            # set error message appropriately
            error = 'You are already following {}!'.format(movie['name'])
        # if not following then insert the follow into the database
        else:
            insert_follow = Follows(
                session['user_id'], movie['id'], movie['name'], movie['release_small'])
            db.session.add(insert_follow)
            db.session.commit()
            # flask success message and re-render template
            flash('Now following {}!'.format(movie['name']), 'success')
            return render_template('search/results.html', results=searchQuery, page=page, pageCount=pageCount, query=id)
        # flash error message
        flash(error, 'warning')
    return render_template('search/results.html', results=searchQuery, page=page, pageCount=pageCount, query=id)


@app.route('/genres/<string:mediaType>/<int:genre>/<genrename>', methods=('GET', 'POST'))
def genres(mediaType, genre, genrename):
    sort_by = request.args.get('sort_by') or 'popularity.desc'
    print(sort_by)
    # three_months_ahead = date.today() + relativedelta(months=+3)
    # three_months_behind = date.today() + relativedelta(months=-3)
    page = request.args.get('page')
    if page is None:
            page = 1
    else:
        page = int(page)
    page_chunk = 2
    if mediaType == 'movie':
        pageCount = requests.get(
            f"https://api.themoviedb.org/3/discover/movie?api_key={configuration.API_KEY_STORAGE}&language=en-US&sort_by={sort_by}&include_adult=false&include_video=false&with_genres={genre}").json()["total_pages"] / page_chunk
        searchQuery = lookupGenre('movie', genre, page, sort_by)
    elif mediaType == 'tv':
        pageCount = requests.get(
            f"https://api.themoviedb.org/3/discover/tv?api_key={configuration.API_KEY_STORAGE}&language=en-US&sort_by={sort_by}&include_adult=false&include_video=false&with_genres={genre}").json()["total_pages"] / page_chunk
        searchQuery = lookupGenre('tv', genre, page, sort_by)
    # use our api to get results based on query string
    # render template with results
    if request.method == 'GET':
        if searchQuery == []:
            error = 'Please refine your search query to be more specific'
        else:
            return render_template('search/genres.html', results=searchQuery, page=page, pageCount=pageCount, genre=genre, genrename=genrename, mediaType=mediaType, sort_by=sort_by)
        flash(error, 'warning')
        return redirect(url_for('index'))
    # if user clicks the follow button
    elif request.method == 'POST':
        # get the movie id from the form and look it up in our api
        id = request.form['movie_id']
        movie = lookupById(id)[0]
        # set error to none
        error = None
        # check to make sure user is not already following
        if db.session.query(Follows).filter(Follows.user_id == session['user_id'], Follows.movie_id == movie['id']).count() > 0:
            # set error message appropriately
            error = 'You are already following {}!'.format(movie['name'])
        # if not following then insert the follow into the database
        else:
            insert_follow = Follows(
                session['user_id'], movie['id'], movie['name'], movie['release_small'])
            db.session.add(insert_follow)
            db.session.commit()
            # flask success message and re-render template
            flash('Now following {}!'.format(movie['name']), 'success')
            return render_template('search/genres.html', results=searchQuery, page=page, pageCount=pageCount, genre=genre, genrename=genrename, mediaType=mediaType)
        # flash error message
        flash(error, 'warning')
    return render_template('search/genres.html', results=searchQuery, page=page, pageCount=pageCount, genre=genre, genrename=genrename, mediaType=mediaType)


@app.route('/profile/<int:id>', methods=('GET', 'POST'))
def profile(id):
    movies = lookupPersonMovies(id)
    profile = lookupPersonProfile(id)
    # if get - then get api information and pass that to the template
    if request.method == 'GET':
        return render_template('search/profile.html', profile=profile, movies=movies, name=profile[0]['name'])

@ app.route('/schedule')
def schedule():
    if request.method == 'GET':
        update_release_dates()
        check_db()
        return render_template('search/schedule.html')

# register route


@ app.route('/auth/register', methods=('GET', 'POST'))
def register():
    # on post - retrieve info from our form
    if request.method == 'POST':
        username = request.form['username'].lower()
        password = request.form['password']
        confirm = request.form['confirm']
        # pre-set error to None
        error = None

        # init regex
        reg = "^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[@$!%*?&-_])[A-Za-z\d@$!#%*?&-_]{6,20}$"
        # Compile Regex
        pat = re.compile(reg)
        # Search Regex
        mat = re.search(pat, password)

        # parse input
        if not username:
            error = 'You must provide a username'
        elif not password:
            error = 'You must provide a password'
        # make sure username doesn't already exist
        if db.session.query(User).filter(User.username == username).count() > 0:
            error = 'User {} is already registered'.format(username)
        # make sure passwords match
        elif password != confirm:
            error = 'Passwords must match'
        elif not mat:
            error = 'Password must contain (one number / one uppercase / one lowercase / one special symbol (@$!%*#?&-_) / be between 6-20 characters'
        # if no errors then insert user into database
        if error is None:
            insert_user = User(username, generate_password_hash(password), confirmed=False)
            db.session.add(insert_user)
            db.session.commit()
            token = generate_confirmation_token(username)
            confirm_url = url_for('confirm_email', token=token, _external=True)
            try:
                send_confirmation_email(username, confirm_url)
            except:
                return redirect(url_for('error_404', error='SMTP Error. Please email moviereleasetracker@gmail.com.'))
            # flash success message
            user = User.query.filter_by(username=username).first()
            session.clear()
            session['user_id'] = user.id
            # redirect to home page
            flash('Now Registered As: {}! Welcome. Please confirm your email.'.format(username), 'success')
            return redirect(url_for('unconfirmed'))
        # flash any errors
        flash(error, 'warning')
    # if get then render template
    return render_template('auth/register.html')

# login route


@ app.route('/auth/login', methods=('POST', 'GET'))
def login():
    next = request.args.get('next')
    if request.method == 'POST':
        # on post - retrieve info from our form
        username = request.form['username'].lower()
        password = request.form['password']
        error = None

        # check for user in database and pull their info if it exists
        user = User.query.filter_by(username=username).first()
        # if valid input and user exists - set our session data
        if user and check_password_hash(user.password, password) == True:
            session.clear()
            session['user_id'] = user.id
            if user.confirmed == False:
                flash('Please confirm your email', 'warning')
            # redirect to home page
            if next == None:
                return redirect(url_for('index'))
            else:
                return redirect(url_for('details', id=next, mediaType='movie'))
        # parse for errors and set error message accordingly
        elif not user or user is None:
            error = 'Invalid Username/Password Combination'
        elif not username or not password:
            error = 'You must fill in both fields'
        else:
            error = 'Invalid Username/Password Combination'
        # flash error if there is one
        flash(error, 'warning')
    # get request renders template
    if g.user is not None:
        return redirect(url_for('index'))
    return render_template('auth/login.html')

# follows route to display users followed movies


@ app.route('/user/follows', methods=('GET', 'POST'))
# ensure user is logged in
@ login_required
@ check_confirmed
def follows():
    # grab users follows from the database and arrange them in order by release date
    if request.method == 'GET':
        follows = db.session.query(Follows).filter(
            Follows.user_id == session['user_id']).order_by(Follows.movie_date.desc()).all()
        followList = []
        # create a list of all users follows to display in the template
        for i in range(len(follows)):
            date_obj = datetime.strptime(follows[i].movie_date, '%Y-%m-%d')
            release = date_obj.strftime('%B %d, %Y')
            released = date_obj.date() <= datetime.now().date()
            title = follows[i].movie_title
            movie_id = follows[i].movie_id
            followList.append({
                "name": title,
                "id": movie_id,
                "release": release,
                "released": released
            })
        # render the template and fill it in with the retrieved info
        return render_template('user/follows.html', follows=followList)
    # if they choose to delete a follow - delete it from their follows
    elif request.method == 'POST':
        # get the movie id from the form (delete button value)
        id = request.form['movie_id']
        # store their session id
        user = g.user.id
        # deletion query
        delete_this = db.session.query(Follows).filter(
            Follows.movie_id == id, Follows.user_id == user).one()
        # delete the entry and commit it
        db.session.delete(delete_this)
        db.session.commit()
        # create an updated follows list
        follows = db.session.query(Follows).filter(
            Follows.user_id == session['user_id']).order_by(Follows.movie_date.desc()).all()
        followList = []
        for i in range(len(follows)):
            date_obj = datetime.strptime(follows[i].movie_date, '%Y-%m-%d')
            release = date_obj.strftime('%B %d, %Y')
            released = date_obj.date() <= datetime.now().date()
            title = follows[i].movie_title
            movie_id = follows[i].movie_id
            followList.append({
                "name": title,
                "id": movie_id,
                "release": release,
                "released": released
            })
        # render the updated template after deletion
        return render_template('user/follows.html', follows=followList)

@app.route('/user/settings', methods=('GET', 'POST'))
@login_required
def settings():
    if request.method == 'GET':
        return render_template('user/settings.html')
    if request.method == 'POST':
        session['adult'] = request.form['adult']
        print(request.form['adult'])
        return render_template('user/settings.html')


# this will store the users id in a global variable accessible anywhere
@ app.before_request
def load_logged_in_user():
    user_id = session.get('user_id')
    if user_id is None:
        g.user = None
    else:
        try:
            user = User.query.filter_by(id=user_id).all()
            g.user = user[0]
        except Exception as e:
            return redirect(url_for('error_404', error='Database error, please email admin @ moviereleasetracker@gmail.com'))

# logout route


@ app.route('/logout')
def logout():
    g.user = None
    session.clear()
    return redirect(url_for('index'))

# Function to make sure release dates are accurate

def update_release_dates():
    # Get follows list from database
    releases = db.session.query(Follows).all()
    # Loop over each item
    for release in releases:
        # Store the movie's id
        database_id = release.movie_id
        # Store the current release date we have in the database
        database_date = release.movie_date
        # Get the updated release date information from the API
        updated_info = lookupReleaseDate(database_id)
        # If the database is different than the API data and not 'TBA'
        if database_date != updated_info['digital']['small'] and updated_info['digital']['small'] != 'TBA':
            # If the database date is also different than the theatre date
            if database_date != updated_info['theatre']['small'] and updated_info['theatre']['small'] != 'TBA':
                # Update the release date to the theatre date
                release.movie_date = updated_info['theatre']['small']
            # If the database date is not different than the theatre date or the theatre date is TBA
            elif updated_info['digital']['small'] != 'TBA':
                # Update the release date to the digital date
                release.movie_date = updated_info['digital']['small']
        # If the database date is the same as the digital date BUT different than the theatre date
        elif database_date != updated_info['theatre']['small'] and updated_info['theatre']['small'] != 'TBA':
            # Update the release date to the theatre date
            release.movie_date = updated_info['theatre']['small']
        db.session.commit()

# function to go over database and find any movie that releases on 'todays' date

def check_db():
    # store todays date value
    today = str(datetime.now().date())
    # grab all releases from follows table that have a movie_date value that matches todays date
    releases = db.session.query(Follows).filter(
        Follows.movie_date == today).all()
    # if none found do nothing
    if releases is None or releases == []:
        pass
    # else send emails to users following a movie that releases today
    else:
        for release in releases:
            # date format
            date_obj = datetime.strptime(release.movie_date, '%Y-%m-%d')
            release_date = date_obj.strftime('%B %d, %Y')
            # get the users email for each release
            to_email = User.query.filter_by(
                id=release.user_id).first().username
            # as well as the movie title
            movie_title = release.movie_title
            # Get the poster for the movie
            movie = lookupById(release.movie_id)
            movie_poster = movie[0]['cover']
            # Get the first two cast members to put in the email
            movie_stars = movie[0]['cast'][0:2]
            if movie_poster != 'https://image.tmdb.org/t/p/w600_and_h900_bestv2None':
                movie_poster_url = movie_poster
            else:
                movie_poster_url = 'http://www.riobeauty.co.uk/images/product_image_not_found.gif'
            # send the email
            try:
                send_release_mail(to_email, release_date, movie_title, movie_poster_url, movie_stars)
            # if a failure occurs - print an error
            except:
                print('Email Error')

# Forgot password route

@ app.route('/auth/forgot', methods=('GET', 'POST'))
def forgot():
    if request.method == 'GET':
        return render_template('auth/forgot.html')
    if request.method == 'POST':
        # Get username input
        username = request.form['username'].lower()
        # Query the database with said username
        user = db.session.query(User).filter(User.username == username).first()
        # If no matches found
        if user is None or user is []:
            error = 'User {} does not exist.'.format(username)
        else:
            # Create reset link
            token = user.get_reset_token()
            link = url_for('reset', token=token, _external=True)
            # Try sending the reset email
            try:
                send_reset_mail(username, link)
                flash('An email has been sent with instructions to reset your password.', 'success')
            except Exception as e:
                # Error redirect
                return redirect(url_for('error_404', error=e))
            return redirect(url_for('login'))
        # Flash the error if there is one
        flash(error, 'warning')
    return render_template('auth/forgot.html')


@ app.route('/auth/reset/<token>', methods=('GET', 'POST'))
def reset(token):
    # Verify the token
    user = User.verify_reset_token(token)
    if request.method == 'GET':
        if user is None:
            flash('That is an invalid or expired token', 'danger')
            return redirect(url_for('forgot'))
        else:
            return render_template('auth/reset.html')
    elif request.method == 'POST':
        password = request.form['password']
        confirm = request.form['confirm']
        # pre-set error to None
        error = None

        # init regex
        reg = "^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[@$!%*#?&-_])[A-Za-z\d@$!#%*?&-_]{6,20}$"
        # Compile Regex
        pat = re.compile(reg)
        # Search Regex
        mat = re.search(pat, password)

        if not password:
            error = 'You must provide a password'
        # make sure passwords match
        elif password != confirm:
            error = 'Passwords must match'
        elif not mat:
            error = 'Password must contain (one number / one uppercase / one lowercase / one special symbol (@$!%*#?&-_) / be between 6-20 characters'
        # if no errors then insert user into database
        if error is None:
            hashed_password = generate_password_hash(password)
            user.password = hashed_password
            db.session.commit()
            # flash success message
            flash('Your Password Has Been Updated! Please Login.', 'success')
            return redirect(url_for('login'))
        # flash any errors
        flash(error, 'warning')
    # if get then render template
    return render_template('auth/reset.html')

# Confirmation email page

@ app.route('/auth/confirm/<token>')
@ login_required
def confirm_email(token):
    try:
        email = confirm_token(token)
    except:
        flash('Confirmation link is invalid or expired.', 'danger')
    user = db.session.query(User).filter(User.username == email).first()
    if user.confirmed:
        flash('Account already confirmed, please login.', 'success')
    else:
        user.confirmed = True
        db.session.commit()
        session.clear()
        session['user_id'] = user.id
        return redirect(url_for('index'))

# Resend confirmation email
@ app.route('/auth/resend')
@ login_required
def resend_confirmation():
    token = generate_confirmation_token(g.user.username)
    confirm_url = url_for('confirm_email', token=token, _external=True)
    try:
        send_confirmation_email(g.user.username, confirm_url)
    except:
        return redirect(url_for('error_404', error='SMTP Error. Please email moviereleasetracker@gmail.com.'))
    flash('A new confirmation email has been sent.', 'success')
    return redirect(url_for('unconfirmed'))

# Unconfirmed email pageCount
@ app.route('/auth/unconfirmed')
@ login_required
def unconfirmed():
    if g.user.confirmed:
        return redirect(url_for('index'))
    return render_template('auth/unconfirmed.html')
# Email confirmation functions

@ app.route('/error/404')
def error_404():
    error = request.args.get('error') or None
    return render_template('error/404.html', error=error)