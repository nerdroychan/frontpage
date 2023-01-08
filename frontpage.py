import os
import sys
import jinja2
import markdown2
import abc
import pathlib
import shutil
import urllib
import tempfile
import http.server
import functools
import socketserver
import datetime
import hashlib

from config import input_dir, output_dir, template_dir, options

env = jinja2.Environment(loader=jinja2.FileSystemLoader(template_dir))
page_template = env.get_template("page.html")
collection_template = env.get_template("collection.html")

markdown_extras = ["fenced-code-blocks", "target-blank-links", "nofollow", "footnotes"]

# for replacing assets url in texts (.md)
def replace_assets_url(text: str) -> str:
    template = jinja2.Environment(loader=jinja2.BaseLoader()).from_string(text)
    return template.render(assets_url=urllib.parse.urljoin(options["url"], options["assets_dir"]))

# for replacing static url in texts (.css)
def replace_static_url(text: str) -> str:
    template = jinja2.Environment(loader=jinja2.BaseLoader()).from_string(text)
    return template.render(static_url=urllib.parse.urljoin(options["url"], "static"))

'''
Page is the base class for every single page on the website, this class should
not be used directly.
'''
class Page(abc.ABC):
    name     = None # the `id` of this page
    title    = None # the human-readable title of this page
    parent   = None # if this page belongs to another page
    src      = None # where to load the source content (relative to input_dir)
    target   = None # where to output the index.html (relative to output_dir)
    content  = None # part between <main></main>
    output   = None # to buffer the processed html for final output
    hidden   = None # whether this page is hidden from public (always rendering)

    @abc.abstractmethod
    def __init__(self, name: str, title: str, hidden: bool = False):
        self.name, self.title, self.hidden = name, title, hidden

    @abc.abstractmethod
    def load(self):
        pass

    def write(self, output_dir: str = output_dir):
        target_path = os.path.join(output_dir, self.target)
        pathlib.Path(target_path).mkdir(parents=True, exist_ok=True)
        path = os.path.join(target_path, "index.html")
        with open(path, "w+") as f:
            f.write(self.output)

    def type_name(self) -> str:
        return self.__class__.__name__

'''
SinglePage is all monolithic page on the website, including the home page.
A SinglePage loads the content from its src and render it using `page.html`.
'''
class SinglePage(Page):
    def __init__(self, name: str, title: str, hidden: bool = False):
        super().__init__(name, title, hidden)
        self.parent = None
        self.src = self.name + ".md"
        self.target = self.name

    def load(self, navs: list):
        src_path = os.path.join(input_dir, self.src)
        with open(src_path, "r") as f:
            self.content = markdown2.markdown(replace_assets_url(f.read()), extras=markdown_extras)
            self.output = page_template.render(options=options, navs=navs, page=self)

'''
FrontPage is the root page of the whole website (known as index page). It's
essentially also a SinglePage but:
    (1) the html title does not render its name
    (2) the page title are hidden
'''
class FrontPage(SinglePage):
    def __init__(self, name: str, title: str):
        super().__init__(name, title)
        self.name = "home"
        self.title = "Home"
        self.target = "" # empty because it generates index.html at root

'''
Collection page is a page that indexes a set of PostPages. It has no source
file and the page content is generated by its posts and rendered by
collection.html.
'''
class CollectionPage(Page):
    posts = None

    def __init__(self, name: str, title: str, hidden: bool = False):
        super().__init__(name, title, hidden)
        self.parent = None
        self.src = self.name
        self.target = self.name
        self.posts = []

    def load(self, navs: list):
        src_path = os.path.join(input_dir, self.src)
        for filename in os.listdir(src_path):
            path = os.path.join(src_path, filename)
            if os.path.isfile(path) and path.endswith(".md"):
                name = os.path.splitext(filename)[0]
                self.posts.append(PostPage(name, name, self))
        for p in self.posts:
            p.load(navs)

        # after the load, each post's title/subtitle and date are ready
        self.posts.sort(key=lambda x: x.datetime, reverse=True)

        self.output = collection_template.render(options=options, navs=navs, page=self, posts=self.posts)

    def write(self, output_dir: str = output_dir):
        for p in self.posts:
            p.write(output_dir)
        super().write(output_dir)

'''
PostPage is a single blog post (that belongs to a collection). It is similar
to a SinglePage but contains extra metadata. During rendering, it also needs
a return button to return to its parent collection page.
'''
class PostPage(Page):
    datetime = None
    date = None
    subtitle = None
    uuid = None

    def __init__(self, name: str, title: str, parent: Page):
        super().__init__(name, title)
        if not parent or not isinstance(parent, CollectionPage):
            raise Exception("post page has no parent or parent is non-collection")
        self.parent = parent
        self.src = os.path.join(self.parent.name, self.name + ".md")
        self.uuid = hashlib.sha1(self.name.encode()).hexdigest()
        self.target = os.path.join(self.parent.name, self.uuid)

    def load(self, navs: list):
        src_path = os.path.join(input_dir, self.src)
        with open(src_path, "r") as f:
            content = markdown2.markdown(replace_assets_url(f.read()), extras=markdown_extras + ["metadata"])
            for k in content.metadata:
                if k == "title":
                    self.title = content.metadata[k]
                elif k == "subtitle":
                    self.subtitle = content.metadata[k]
                elif k == "date":
                    date = content.metadata[k]
                    self.datetime = datetime.datetime.strptime(date, "%m-%d-%Y")
                    self.date = self.datetime.strftime("%b %-d, %Y")
                elif k == "hidden":
                    if content.metadata[k].lower() == "true":
                        self.hidden = True
            self.content = content
            self.output = page_template.render(options=options, navs=navs, page=self)

def build_skeleton(output_dir: str = output_dir):
    try:
        shutil.rmtree(output_dir)
    except:
        pass
    pathlib.Path(output_dir).mkdir(parents=True, exist_ok=True)
    shutil.copytree(os.path.join(template_dir, "static"), os.path.join(output_dir, "static"))
    shutil.copytree(options["assets_dir"], os.path.join(output_dir, "assets"))
    css = ""
    with open(os.path.join(output_dir, "static", "styles.css"), "r") as f:
        css = replace_static_url(f.read())
    with open(os.path.join(output_dir, "static", "styles.css"), "w") as f:
        f.write(css)

def build_site(pages: list, output_dir: dir = output_dir):
    # only page in pages will show in nav bar
    navs = [{"name": p.name, "title": p.title, "type_name": p.type_name()} for p in pages if not p.hidden]
    build_skeleton(output_dir)

    for p in pages:
        p.load(navs)

    for p in pages:
        p.write(output_dir)

def start_test_server(path: str):
    os.chdir(path)
    handler = http.server.SimpleHTTPRequestHandler
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("", 8888), handler)
    print("Starting test server at path", path)
    print("Test server serving at port", 8888)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()
    finally:
        httpd.shutdown()

if __name__ == "__main__":
    # define all the pages here (must exist on disk with correct format)
    from pages import pages

    # test mode, if specified
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        with tempfile.TemporaryDirectory() as tmpdir:
            options["url"] = "http://127.0.0.1:8888"
            output_dir = tmpdir
            build_site(pages, tmpdir)
            start_test_server(tmpdir)
    else:
        build_site(pages)
