from django.db import connection
# Getting the SpatialBackend container and the geographic quoting method.
from django.contrib.gis.db.backend import SpatialBackend, gqn
# GeometryProxy, GEOS, Distance, and oldforms imports.
from django.contrib.gis.db.models.proxy import GeometryProxy
from django.contrib.gis.measure import Distance
from django.contrib.gis.oldforms import WKTField

# The `get_srid_info` function gets SRID information from the spatial
# reference system table w/o using the ORM.
from django.contrib.gis.models import get_srid_info

#TODO: Flesh out widgets; consider adding support for OGR Geometry proxies.
class GeometryField(SpatialBackend.Field):
    "The base GIS field -- maps to the OpenGIS Specification Geometry type."

    # The OpenGIS Geometry name.
    _geom = 'GEOMETRY'

    # Geodetic units.
    geodetic_units = ('Decimal Degree', 'degree')

    def __init__(self, srid=4326, spatial_index=True, dim=2, **kwargs):
        """
        The initialization function for geometry fields.  Takes the following
        as keyword arguments:

        srid:
         The spatial reference system identifier, an OGC standard.
         Defaults to 4326 (WGS84).

        spatial_index:
         Indicates whether to create a spatial index.  Defaults to True.
         Set this instead of 'db_index' for geographic fields since index
         creation is different for geometry columns.
                  
        dim:
         The number of dimensions for this geometry.  Defaults to 2.
        """

        # Setting the index flag with the value of the `spatial_index` keyword.
        self._index = spatial_index

        # Setting the SRID and getting the units.  Unit information must be 
        # easily available in the field instance for distance queries.
        self._srid = srid
        self._unit, self._unit_name, self._spheroid = get_srid_info(srid)

        # Setting the dimension of the geometry field.
        self._dim = dim

        super(GeometryField, self).__init__(**kwargs) # Calling the parent initializtion function

    ### Routines specific to GeometryField ###
    @property
    def geodetic(self):
        """
        Returns true if this field's SRID corresponds with a coordinate
        system that uses non-projected units (e.g., latitude/longitude).
        """
        return self._unit_name in self.geodetic_units

    def get_distance(self, dist_val, lookup_type):
        """
        Returns a distance number in units of the field.  For example, if 
        `D(km=1)` was passed in and the units of the field were in meters,
        then 1000 would be returned.
        """
        # Getting the distance parameter and any options.
        if len(dist_val) == 1: dist, option = dist_val[0], None
        else: dist, option = dist_val

        if isinstance(dist, Distance):
            if self.geodetic:
                # Won't allow Distance objects w/DWithin lookups on PostGIS.
                if SpatialBackend.postgis and lookup_type == 'dwithin':
                    raise TypeError('Only numeric values of degree units are allowed on geographic DWithin queries.')
                # Spherical distance calculation parameter should be in meters.
                dist_param = dist.m
            else:
                dist_param = getattr(dist, Distance.unit_attname(self._unit_name))
        else:
            # Assuming the distance is in the units of the field.
            dist_param = dist
       
        if SpatialBackend.postgis and self.geodetic and lookup_type != 'dwithin' and option == 'spheroid':
            # On PostGIS, by default `ST_distance_sphere` is used; but if the 
            # accuracy of `ST_distance_spheroid` is needed than the spheroid 
            # needs to be passed to the SQL stored procedure.
            return [gqn(self._spheroid), dist_param]
        else:
            return [dist_param]

    def get_geometry(self, value):
        """
        Retrieves the geometry, setting the default SRID from the given
        lookup parameters.
        """
        if isinstance(value, (tuple, list)): 
            geom = value[0]
        else:
            geom = value

        # When the input is not a GEOS geometry, attempt to construct one
        # from the given string input.
        if isinstance(geom, SpatialBackend.Geometry):
            pass
        elif isinstance(geom, basestring):
            try:
                geom = SpatialBackend.Geometry(geom)
            except SpatialBackend.GeometryException:
                raise ValueError('Could not create geometry from lookup value: %s' % str(value))
        else:
            raise TypeError('Cannot use parameter of `%s` type as lookup parameter.' % type(value))

        # Assigning the SRID value.
        geom.srid = self.get_srid(geom)
        
        return geom

    def get_srid(self, geom):
        """
        Has logic for retrieving the default SRID taking into account 
        the SRID of the field.
        """
        if geom.srid is None or (geom.srid == -1 and self._srid != -1):
            return self._srid
        else:
            return geom.srid

    ### Routines overloaded from Field ###
    def contribute_to_class(self, cls, name):
        super(GeometryField, self).contribute_to_class(cls, name)
        
        # Setup for lazy-instantiated Geometry object.
        setattr(cls, self.attname, GeometryProxy(SpatialBackend.Geometry, self))

    def get_db_prep_lookup(self, lookup_type, value):
        """
        Returns the spatial WHERE clause and associated parameters for the
        given lookup type and value.  The value will be prepared for database
        lookup (e.g., spatial transformation SQL will be added if necessary).
        """
        if lookup_type in SpatialBackend.gis_terms:
            # special case for isnull lookup
            if lookup_type == 'isnull': return [], []

            # Get the geometry with SRID; defaults SRID to that of the field
            # if it is None.
            geom = self.get_geometry(value)

            # Getting the WHERE clause list and the associated params list. The params 
            # list is populated with the Adaptor wrapping the Geometry for the 
            # backend.  The WHERE clause list contains the placeholder for the adaptor
            # (e.g. any transformation SQL).
            where = [self.get_placeholder(geom)]
            params = [SpatialBackend.Adaptor(geom)]

            if isinstance(value, (tuple, list)):
                if lookup_type in SpatialBackend.distance_functions:
                    # Getting the distance parameter in the units of the field.
                    where += self.get_distance(value[1:], lookup_type)
                elif lookup_type in SpatialBackend.limited_where:
                    pass
                else:
                    # Otherwise, making sure any other parameters are properly quoted.
                    where += map(gqn, value[1:])
            return where, params
        else:
            raise TypeError("Field has invalid lookup: %s" % lookup_type)

    def get_db_prep_save(self, value):
        "Prepares the value for saving in the database."
        if isinstance(value, SpatialBackend.Geometry):
            return SpatialBackend.Adaptor(value)
        elif value is None:
            return None
        else:
            raise TypeError('Geometry Proxy should only return Geometry objects or None.')

    def get_manipulator_field_objs(self):
        "Using the WKTField (oldforms) to be our manipulator."
        return [WKTField]

# The OpenGIS Geometry Type Fields
class PointField(GeometryField):
    _geom = 'POINT'

class LineStringField(GeometryField):
    _geom = 'LINESTRING'

class PolygonField(GeometryField):
    _geom = 'POLYGON'

class MultiPointField(GeometryField):
    _geom = 'MULTIPOINT'

class MultiLineStringField(GeometryField):
    _geom = 'MULTILINESTRING'

class MultiPolygonField(GeometryField):
    _geom = 'MULTIPOLYGON'

class GeometryCollectionField(GeometryField):
    _geom = 'GEOMETRYCOLLECTION'
