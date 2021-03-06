import hashlib
import rdflib
from rdflib import collection as rdfcollection
from django.utils.text import slugify

from belfastdata.rdfns import BIBO, DC, SCHEMA_ORG, BG, BELFAST_GROUP_URI


class SmushGroupSheets(object):

    # base identifier for 'smushed' ids
    BELFASTGROUPSHEET = rdflib.Namespace("http://belfastgroup.library.emory.edu/groupsheets/md5/")

    def __init__(self, files):
        for f in files:
            self.process_file(f)

    def calculate_uri(self, uri, graph):
        # calculate a 'smushed' uri for a single groupsheet
        titles = []
        title = graph.value(uri, DC.title)

        # title is either a single literal OR an rdf sequence
        if title:
            # single literal
            if isinstance(title, rdflib.Literal):
                titles.append(title)

            # otherwise, assuming node is an rdf sequence
            else:
                # convert from resource to standard blank node
                # since collection doesn't seem to handle resource
                # create a collection to allow treating as a list
                titles.extend(rdfcollection.Collection(graph,
                                                       title))

        # ignore title order for the purposes of de-duping
        # - sort titles so we can get a consistent MD5
        #   (assumes any group sheet with the same titles in any order
        #    and the same author is equivalent)
        # - slugify titles so we can ignore discrepancies in case and punctuation
        titles = sorted([slugify(t) for t in titles])

        author = graph.value(uri, SCHEMA_ORG.author)
        # blank node for the author is unreliable...
        if isinstance(author, rdflib.BNode):
            # This should mostly only occur in Queen's University Belfast,
            # where we don't have URIs but *do* have first & last names.
            # Construct lastname, first for author identifier
            # (Assumes we are using a VIAF URI wherever possible, which
            # should be the case.)
            last = graph.value(author, SCHEMA_ORG.familyName)
            first = graph.value(author, SCHEMA_ORG.givenName)
            if last is not None and first is not None:
                author = '%s, %s' % (last, first)
            else:
                author = None

        # if not at least one title or title and author, skip this ms
        if not titles and not author:
            return

        m = hashlib.md5()
        if author is None:
            author = 'anonymous'
        text = '%s %s' % (author, ' '.join(titles))
        m.update(text.encode('utf-8'))

        return self.BELFASTGROUPSHEET[m.hexdigest()]

    def process_file(self, filename):
        # build a dictionary of "smushed" URIs for belfast group sheets
        # for this document
        new_uris = {}

        g = rdflib.Graph()
        g.parse(filename)

        # smushing should be done after infer/identify group sheets
        # and assign local group sheet type
        # SO - simply find by our belfast group sheet type

        ms = list(g.subjects(predicate=rdflib.RDF.type, object=BG.GroupSheet))
        # if no manuscripts are found, stop and do not update the file
        if len(ms) == 0:
            # possibly print out in a verbose mode if we add that
            #print 'No groupsheets found in %s' % filename
            return

        # TEMP / sanity check
        print 'Found %d groupsheet%s in %s' % \
            (len(ms), 's' if len(ms) != 1 else '', filename)

        for m in ms:
            # FIXME: only calculate a new uri for blank nodes?
            # TODO: handle TEI-based rdf with ARK pid urls
            newURI = self.calculate_uri(m, g)
            if newURI is not None:
                new_uris[m] = newURI

        output = rdflib.Graph()
        # bind namespace prefixes from the input graph
        for prefix, ns in g.namespaces():
            output.bind(prefix, ns)

        # iterate over all triples in the old graph and convert
        # any uris in the new_uris dictionary to the smushed identifier
        for s, p, o in g:
            s = new_uris.get(s, s)
            # don't convert a smushed URL (e.g., TEI groupsheet URL)
            if not p == SCHEMA_ORG.URL:
                o = new_uris.get(o, o)
            output.add((s, p, o))

        # NOTE: currently replaces the starting file.  Might not be ideal,
        # but may actually be reasonable for the currently intended use.
        # print 'Replacing %s' % filename
        with open(filename, 'w') as datafile:
            output.serialize(datafile)


class IdentifyGroupSheets(object):

    def __init__(self, files):
        for f in files:
            self.process_file(f)

    def process_file(self, filename):
        # identify belfast group sheets and label them with our local
        # belfast group sheet type

        g = rdflib.Graph()
        g.parse(filename)

        # some collections include group sheets mixed with other content
        # (irishmisc, ormsby)
        # first look for a manuscript with an author that directly
        # references the belfast group
        res = g.query('''
            PREFIX schema: <%(schema)s>
            PREFIX rdf: <%(rdf)s>
            PREFIX bibo: <%(bibo)s>
            SELECT ?ms
            WHERE {
                ?ms rdf:type bibo:Manuscript .
                ?ms schema:mentions <%(belfast_group)s> .
                ?ms schema:author ?auth
            }
            ''' % {'schema': SCHEMA_ORG,
                   'rdf': rdflib.RDF,
                   'bibo': BIBO,
                   'belfast_group': BELFAST_GROUP_URI
                   }
            )
            # searching for all manuscript that 'mention' belfast group
            # NOTE: schema:mentions NOT the right relation here;
            # needs to be fixed in findingaids and then here

        # if no matches, do a greedier search
        if len(res) == 0:

            # Find every manuscript mentioned in a document
            # that is *about* the belfast group
            # TODO: will also need to find ms associated with / presented at BG
            # NOTE: need a way to filter non-belfast group content
            res = g.query('''
                PREFIX schema: <%(schema)s>
                PREFIX rdf: <%(rdf)s>
                PREFIX bibo: <%(bibo)s>
                SELECT ?ms
                WHERE {
                    ?doc schema:about <%(belfast_group)s> .
                    ?doc schema:mentions ?ms .
                    ?ms rdf:type bibo:Manuscript .
                }
                ''' % {'schema': SCHEMA_ORG,
                       'rdf': rdflib.RDF,
                       'bibo': BIBO,
                       'belfast_group': BELFAST_GROUP_URI
                       }
            )
            # TODO: how to filter out non-group sheet irish misc content?
            # FIXME: not finding group sheets in irishmisc! (no titles?)

        # if no manuscripts are found, stop and do not update the file
        if len(res) == 0:
            # possibly print out in a verbose mode if we add that
            # print 'No groupsheets found in %s' % filename
            return

        print 'Found %d groupsheet%s in %s' % \
            (len(res), 's' if len(res) != 1 else '', filename)

        for r in res:
            g.add((r['ms'], rdflib.RDF.type, BG.GroupSheet))

        #print 'Replacing %s' % filename
        with open(filename, 'w') as datafile:
            g.serialize(datafile)


class InferConnections(object):

    def __init__(self, files):
        for f in files:
            self.process_file(f)

    def process_file(self, filename):
        # identify belfast group sheets and label them with our local
        # belfast group sheet type

        g = rdflib.Graph()
        g.parse(filename)

        ms = list(g.subjects(predicate=rdflib.RDF.type, object=BG.GroupSheet))
        # if no manuscripts are found, skip
        if len(ms) == 0:
            return

        res = g.query('''
                PREFIX schema: <%(schema)s>
                PREFIX rdf: <%(rdf)s>
                PREFIX bg: <%(bg)s>
                SELECT ?author
                WHERE {
                    ?ms schema:author ?author .
                    ?ms rdf:type bg:GroupSheet
                }
                ''' % {'schema': SCHEMA_ORG,
                       'rdf': rdflib.RDF,
                       'bg': BG}
        )
        modified = False
        for r in res:
            # triple to indicate the author is affiliated with BG
            bg_assoc = (r['author'], SCHEMA_ORG.affiliation, rdflib.URIRef(BELFAST_GROUP_URI))
            if bg_assoc not in g:
                modified = True
                g.add(bg_assoc)

        if modified:
            with open(filename, 'w') as datafile:
                g.serialize(datafile)

