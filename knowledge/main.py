# -*- coding: utf-8 -*-
# This Blueprint is about adding a Knowledge Base to the LMFDB website.
# referencing content, dynamically inserting information into the website, …
# 
# This is more than just a web of entries in a wiki, because content is "transcluded".
# Transclusion is an actual concept, you can read about it here:
# http://en.wikipedia.org/wiki/Transclusion
#
# a "Knowl" (see knowl.py) is our base class for any bit of "knowledge". we might
# subclass it into "theorem", "proof", "description", and much more if necessary
# (i.e. when it makes sense to add additional fields, e.g. for referencing each other)
#
# author: Harald Schilly <harald.schilly@univie.ac.at>
import string
import pymongo
import flask
from base import app, getDBConnection
from datetime import datetime
from flask import render_template, render_template_string, request, abort, Blueprint, url_for, make_response
from flaskext.login import login_required, current_user
from knowl import Knowl, knowl_title, get_history
from users import admin_required, housekeeping
import markdown
from knowledge import logger

ASC = pymongo.ASCENDING
DSC = pymongo.DESCENDING

import re
allowed_knowl_id = re.compile("^[a-z0-9._-]+$")

# Tell markdown to not escape or format inside a given block
class IgnorePattern(markdown.inlinepatterns.Pattern):
    def handleMatch(self, m):
        return markdown.AtomicString(m.group(2))

class HashTagPattern(markdown.inlinepatterns.Pattern):
    def handleMatch(self, m):
      el = markdown.etree.Element("a")
      el.set('href', url_for('.index')+'?search=%23'+m.group(2))
      el.text = '#' + markdown.AtomicString(m.group(2))
      return el

class KnowlTagPattern(markdown.inlinepatterns.Pattern):
  def handleMatch(self, m):
    kid = m.group(2)
    #el = markdown.etree.Element("span")
    #el.text = markdown.AtomicString("{{ KNOWL('%s') }}" % kid)
    #return el
    return "{{ KNOWL('%s') }}" % kid

# Initialise the markdown converter, sending a wikilink [[topic]] to the L-functions wiki
md = markdown.Markdown(extensions=['wikilinks'],
    extension_configs = {'wikilinks': [('base_url', 'http://wiki.l-functions.org/')]})
# Prevent $..$, $$..$$, \(..\), \[..\] blocks from being processed by Markdown
md.inlinePatterns.add('mathjax$', IgnorePattern(r'(?<![\\\$])(\$[^\$].*?\$)'), '<escape')
md.inlinePatterns.add('mathjax$$', IgnorePattern(r'(?<![\\])(\$\$.+?\$\$)'), '<escape')
md.inlinePatterns.add('mathjax\\(', IgnorePattern(r'(\\\(.+?\\\))'), '<escape')
md.inlinePatterns.add('mathjax\\[', IgnorePattern(r'(\\\[.+?\\\])'), '<escape')

# Tell markdown to turn hashtags into search urls
hashtag_keywords_rex = r'#([a-zA-Z][a-zA-Z0-9-_]{1,})\b'
md.inlinePatterns.add('hashtag', HashTagPattern(hashtag_keywords_rex), '<escape')

# Tell markdown to do some magic for including knowls
knowltag_regex = r'\[\[[ ]*([a-z.-_]+)[ ]*\]\]'
md.inlinePatterns.add('knowltag', KnowlTagPattern(knowltag_regex),'<escape')

# global (application wide) insertion of the variable "Knowl" to create
# lightweight Knowl objects inside the templates.
@app.context_processor
def ctx_knowledge():
  return {'Knowl' : Knowl, 'knowl_title' : knowl_title}

@app.template_filter("render_knowl")
def render_knowl_in_template(knowl_content, **kwargs):
  """
  This function does the actual rendering, for render and the template_filter
  render_knowl_in_template (ultimately for KNOWL_INC)
  """
  render_me = u"""\
  {%% include "knowl-defs.html" %%}
  {%% from "knowl-defs.html" import KNOWL with context %%}
  {%% from "knowl-defs.html" import KNOWL_LINK with context %%}
  {%% from "knowl-defs.html" import KNOWL_INC with context %%}
  {%% from "knowl-defs.html" import TEXT_DATA with context %%}

  %(content)s
  """
  # markdown enabled
  render_me = render_me % {'content' : md.convert(knowl_content) }
  # Pass the text on to markdown.  Note, backslashes need to be escaped for this, but not for the javascript markdown parser
  try:
    return render_template_string(render_me, **kwargs)
  except Exception, e:
    return "ERROR in the template: %s. Please edit it to resolve the problem." % e
  

# a jinja test for figuring out if this is a knowl or not
# usage: {% if K is knowl_type %} ... {% endif %}
def test_knowl_type(k):
  return isinstance(k, Knowl)
app.jinja_env.tests['knowl_type'] = test_knowl_type

from knowledge import knowledge_page

# blueprint specific definition of the body_class variable
@knowledge_page.context_processor
def body_class():
  return { 'body_class' : 'knowl' }

def get_bread(breads = []):
  bc = [("Knowledge", url_for(".index"))]
  for b in breads:
    bc.append(b)
  return bc

def searchbox(q="", clear=False):
  """returns the searchbox"""
  searchbox = u"""\
    <form id='knowl-search' action="%s" method="GET">
      <input name="search" value="%s" />"""
  if clear:
    searchbox += '<a href="%s">clear</a>' % url_for(".index")
  searchbox += '<button type="submit">Go</button>'
  searchbox += "</form>" 
  return searchbox % (url_for(".index"), q)

@knowledge_page.route("/test")
def test():
  """
  just a test page
  """
  logger.info("test")
  return render_template("knowl-test.html",
               bread=get_bread([("Test", url_for(".test"))]), 
               title="Knowledge Test",
               k1 = Knowl("k1"))

@knowledge_page.route("/edit/<ID>")
@login_required
def edit(ID):
  if not allowed_knowl_id.match(ID):
      flask.flash("""Oops, knowl id '%s' is not allowed.
                  It must consist of lower/uppercase characters, 
                  no spaces, numbers or '.', '_' and '-'.""" % ID, "error")
      return flask.redirect(url_for(".index"))
  knowl = Knowl(ID)

  from knowl import is_locked, set_locked
  lock = False
  if request.args.get("lock", "") != 'ignore':
    lock = is_locked(knowl.id)
  # lock, if either lock is false or (lock is active), current user is editing again
  author_edits = lock and lock['who'] == current_user.get_id()
  logger.debug(author_edits)
  if not lock or author_edits:
    set_locked(knowl, current_user.get_id())
  if author_edits: lock = False
    
  b = get_bread([("Edit '%s'"%ID, url_for('.edit', ID=ID))])
  return render_template("knowl-edit.html", 
         title="Edit Knowl '%s'" % ID,
         k = knowl,
         bread = b,
         lock = lock)

@knowledge_page.route("/show/<ID>")
def show(ID):
  k = Knowl(ID)
  r = render(ID, footer="0", raw=True)
  title = k.title or "'%s'" % k.id
  b = get_bread([('%s'%title, url_for('.show', ID=ID))])
    
  return render_template("knowl-show.html",
         title = k.title,
         k = k,
         render = r,
         bread = b)

@knowledge_page.route("/history")
def history():
  h_items = get_history()
  bread = get_bread([("History", url_for('.history'))])
  return render_template("knowl-history.html", 
                         title="Knowledge History",
                         bread = bread,
                         history = h_items)

@knowledge_page.route("/delete/<ID>")
@admin_required
def delete(ID):
  k = Knowl(ID)
  k.delete()
  flask.flash("Snif! Knowl %s deleted and gone forever :-(" % ID)
  return flask.redirect(url_for(".index"))

@knowledge_page.route("/edit", methods=["POST"])
@login_required
def edit_form():
  ID = request.form['id']
  return flask.redirect(url_for(".edit", ID=ID))

@knowledge_page.route("/save", methods=["POST"])
@login_required
def save_form():
  ID = request.form['id']
  if not ID:
    raise Exception("no id")

  if not allowed_knowl_id.match(ID):
      flask.flash("""Oops, knowl id '%s' is not allowed.
                  It must consist of lower/uppercase characters, 
                  no spaces, numbers or '.', '_' and '-'.""" % ID, "error")
      return flask.redirect(url_for(".index"))

  k = Knowl(ID)
  k.title = request.form['title']
  k.content = request.form['content']
  k.quality = request.form['quality']
  k.timestamp = datetime.now()
  k.save(who=current_user.get_id())
  from knowl import save_history
  save_history(k, current_user.get_id())
  return flask.redirect(url_for(".show", ID=ID))
  

@knowledge_page.route("/render/<ID>", methods = ["GET", "POST"])
def render(ID, footer=None, kwargs = None, raw = False):
  """
  this method renders the given Knowl (ID) to insert it
  dynamically in a website. It is intended to be used 
  by an AJAX call, but should do a similar job server-side
  only, too.

  Note, that the used knowl-render.html template is *not*
  based on any globally defined website and just creates
  a small and simple html snippet!

  the keyword 'raw' is used in knowledge.show and knowl_inc to
  include *just* the string and not the response object.
  """
  k = Knowl(ID)

  #logger.debug("kwargs: %s", request.args)
  kwargs = kwargs or dict(((k, v) for k,v in request.args.iteritems()))
  #logger.debug("kwargs: %s" , kwargs)

  #this is a very simple template based on no other template to render one single Knowl
  #for inserting into a website via AJAX or for server-side operations.
  if request.method == "POST":
    con = request.form['content']
    foot = footer or request.form['footer']
  elif request.method == "GET":
    con = request.args.get("content", k.content)
    foot = footer or request.args.get("footer", "1") 

  #authors = []
  #for a in k.author_links():
  #  authors.append("<a href='%s'>%s</a>" % 
  #    (url_for('users.profile', userid=a['_id']), a['full_name'] or a['_id'] ))
  #authors = ', '.join(authors)

  render_me = u"""\
  {%% include "knowl-defs.html" %%}
  {%% from "knowl-defs.html" import KNOWL with context %%}
  {%% from "knowl-defs.html" import KNOWL_LINK with context %%}
  {%% from "knowl-defs.html" import KNOWL_INC with context %%}
  {%% from "knowl-defs.html" import TEXT_DATA with context %%}

  <div class="knowl">"""
  if foot == "1":
    render_me += """\
  <div class="knowl-header">
    <a href="{{ url_for('.show', ID='%(ID)s') }}">%(title)s</a> 
  </div>""" % { 'ID' : k.id, 'title' : (k.title or k.id) }

  render_me += """<div><div class="knowl-content">%(content)s</div></div>"""

  if foot == "1": 
    render_me += """\
  <div class="knowl-footer">
    <a href="{{ url_for('.show', ID='%(ID)s') }}">permalink</a> 
    {%% if user_is_authenticated %%}
      &middot;
      <a href="{{ url_for('.edit', ID='%(ID)s') }}">edit</a> 
    {%% endif %%}
  </div>"""
  # """ &middot; Authors: %(authors)s """
  render_me += "</div>"
  # render_me = render_me % {'content' : con, 'ID' : k.id }
  # markdown enabled
  render_me = render_me % {'content' : md.convert(con), 'ID' : k.id } #, 'authors' : authors }
  # Pass the text on to markdown.  Note, backslashes need to be escaped for this, but not for the javascript markdown parser
  
  #logger.debug("rendering template string:\n%s" % render_me)

  # TODO wrap this string-rendering into a try/catch and return a proper error message
  # so that the user has a clue. Most likely, the {{ KNOWL('...') }} has the wrong syntax!
  try:
    data = render_template_string(render_me, k = k, **kwargs)
    if raw: return data
    resp = make_response(data)
    # cache 10 minutes if it is a usual GET
    if request.method == 'GET':
      resp.headers['Cache-Control'] = 'max-age=%s, public' % (10 * 60)
    return resp
  except Exception, e:
    return "ERROR in the template: %s. Please edit it to resolve the problem." % e

@knowledge_page.route("/_cleanup")
@housekeeping
def cleanup():
  """
  reindexes knowls, also the list of categories. prunes history.
  this is an internal task just for admins!
  """
  from knowl import refresh_knowl_categories, extract_cat, make_keywords, get_knowls
  cats = refresh_knowl_categories()
  knowls = get_knowls()
  q_knowls = knowls.find(fields=['content', 'title'])
  for k in q_knowls:
    kid = k['_id']
    cat = extract_cat(kid)
    search_keywords = make_keywords(k['content'], kid, k['title'])
    knowls.update({'_id' : kid}, 
                  {"$set": { 
                     'cat' : cat,
                     '_keywords' :  search_keywords
                   }})

  hcount = 0
  # max allowed history length
  max_h = 50 
  q_knowls = knowls.find({'history' : {'$exists' : True}}, fields=['history'])
  for k in q_knowls:
    if len(k['history']) <= max_h: continue
    hcount += 1
    knowls.update({'_id':k['_id']}, {'$set' : {'history' : k['history'][-max_h:]}})

  return "categories: %s <br/>reindexed %s knowls<br/>pruned %s histories" % (cats, q_knowls.count(), hcount)

@knowledge_page.route("/")
def index():
  # bypassing the Knowl objects to speed things up
  from knowl import get_knowls
  get_knowls().ensure_index('_keywords')
  get_knowls().ensure_index('cat')

  cur_cat = request.args.get("category", "")
  
  qualities = []
  defaults      = "filter" not in request.args
  filtermode    = "filter" in request.args
  searchmode    = "search" in request.args
  categorymode  = "category" in request.args

  from knowl import knowl_qualities
  # TODO wrap this into a loop:
  reviewed = request.args.get("reviewed", "") == "on" or defaults
  ok       = request.args.get("ok", "") == "on"       or defaults
  beta     = request.args.get("beta", "") == "on"     or defaults

  if reviewed: qualities.append("reviewed")
  if ok:       qualities.append("ok")
  if beta:     qualities.append("beta")

  s_query = {}

  if filtermode:
    quality_q = { '$in' : qualities }
    s_query['quality'] = quality_q
  
  keyword = request.args.get("search", "").lower()
  if searchmode and keyword:
    keywords = filter(lambda _:len(_) >= 3, keyword.split(" "))
    #logger.debug("keywords: %s" % keywords)
    keyword_q = {'_keywords' : { "$all" : keywords}}
    s_query.update(keyword_q)

  if categorymode:
    s_query.update({ 'cat' : cur_cat }) #{ "$regex" : r"^%s\..+" % cur_cat }

  logger.debug("search query: %s" % s_query)
  knowls = get_knowls().find(s_query, fields=['title'])

  def first_char(k):
    t = k['title']
    if len(t) == 0: return "?"
    if t[0] not in string.ascii_letters: return "?"
    return t[0].upper()

  # way to additionally narrow down the search
  # def incl(knwl):
  #   if keyword in knwl['_id'].lower():   return True
  #   if keyword in knwl['title'].lower(): return True
  #   return False
  # if keyword: knowls = filter(incl, knowls)
  
  from knowl import get_categories 
  cats = get_categories()

  knowls = sorted(knowls, key = lambda x : x['title'].lower())
  from itertools import groupby
  knowls = groupby(knowls, first_char)
  return render_template("knowl-index.html", 
         title  = "Knowledge Database",
         bread  = get_bread(),
         knowls = knowls,
         search = keyword,
         searchbox = searchbox(request.args.get("search", ""), searchmode),
         knowl_qualities = knowl_qualities,
         searchmode = searchmode,
         filters = (beta, ok, reviewed),
         categories = cats,
         cur_cat = cur_cat,
         categorymode = categorymode)


