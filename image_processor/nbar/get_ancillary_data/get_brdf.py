#!/usr/bin/env python

import commands
import datetime
import os
import re

from ULA3.image_processor import constants
from ULA3.ancillary.brdf import BRDFLoader
from ULA3.ancillary.brdf import get_brdf_dirs_modis
from ULA3.ancillary.brdf import get_brdf_dirs_pre_modis
from ULA3.ancillary.brdf import read_subset

# Until we sort out an IO mechanism for rasterio
# we can use this.  It is simple and easy and does internal datatype
# conversions
from ULA3.tests import unittesting_tools as ut

def findFile(fileList, bandWL, factor):
    for file in fileList:
        if file.find(bandWL)!= -1 and file.find(factor)!= -1:
            return file
    return None


def get_brdf_data(extents, satellite, sensor, Date, brdf_primary_path,
                  brdf_secondary_path, work_path):
    """
    Calculates the mean BRDF value for each band wavelength of your
    sensor, for each BRDF factor ['geo', 'iso', 'vol'] that covers
    your image extents.

    :param extents:
        A dictionary containing (x, y) tuples for the 4 corners of
        an image. The dictionary should contain the following keys:
        UL -> Upper Left
        UR -> Upper Right
        LR -> Lower Right
        LL -> Lower Left

    :param satellite:
        A string containing the name of the satellite to which your
        images belong.

    :param sensor:
        A string containing the name of the sensor to which your
        images belong.

    :param Date:
        A datetime.date object representing your image acquistion.

    :param brdf_primary_path:
        A string containing the full file system path to your directory
        containing the source BRDF files.  The BRDF directories are
        assumed to be yyyy.mm.dd naming convention.

    :param brdf_secondary_path:
        A string containing the full file system path to your directory
        containing the Jupp-Li backup BRDF data.  To be used for
        pre-MODIS and potentially post-MODIS acquisitions.

    :param work_path:
        A string containing the full file system path to your NBAR
        working directory. Intermediate BRDF files will be saved to
        work_path/brdf_intermediates/.

    :return:
        A dictionary with tuple (band, factor) as the keys. Each key
        represents the band of your satllite/sensor and brdf factor.
        Each key contains a dictionary with the following keys:
        data_source -> BRDF
        data_file -> File system path to the location of the selected
            BRDF wavelength and factor combination.
        value -> The mean BRDF value covering your image extents.
    """

    # Get the required BRDF LUT & factors list
    nbar_constants = constants.NBARConstants(satellite, sensor)
    
    brdf_lut = nbar_constants.getBRDFlut()
    brdf_factors = nbar_constants.getBRDFfactors()


    # Get the boundary extents of the image
    # Each is a co-ordinate pair of (x, y)
    UL_Lon = extents['UL'][0]
    UL_Lat = extents['UL'][1]
    UR_Lon = extents['UR'][0]
    UR_Lat = extents['UR'][1]
    LR_Lon = extents['LR'][0]
    LR_Lat = extents['LR'][1]
    LL_Lon = extents['LL'][0]
    LL_Lat = extents['LL'][1]


    # Use maximal axis-aligned extents for BRDF mean value calculation.
    # Note that latitude min-max logic is valid for the Southern
    # hemisphere only.
    nw = (min(UL_Lon, LL_Lon), max(UL_Lat, UR_Lat))
    se = (max(LR_Lon, UR_Lon), min(LL_Lat, LR_Lat))


    # Compare the scene date and MODIS BRDF start date to select the 
    # BRDF data root directory.
    # Scene dates outside the range of the CSIRO mosaic data
    # (currently 2000-02-18 through 2013-01-09) should use the pre-MODIS,
    # Jupp-Li BRDF.
    brdf_dir_list  = sorted(os.listdir(brdf_primary_path))
    brdf_dir_range = [brdf_dir_list[0], brdf_dir_list[-1]]
    brdf_range     = [datetime.date(*[int(x) for x in y.split('.')])
                      for y in brdf_dir_range]

    use_JuppLi_brdf = (Date < brdf_range[0] or Date > brdf_range[1])

    if use_JuppLi_brdf:
        brdf_base_dir = brdf_secondary_path
        brdf_dirs = get_brdf_dirs_pre_modis(brdf_base_dir, Date)
    else:
        brdf_base_dir = brdf_primary_path
        brdf_dirs = get_brdf_dirs_modis(brdf_base_dir, Date)


    # The following hdfList code was resurrected from the old SVN repo. JS
    # get all HDF files in the input dir
    dbDir = os.path.join(brdf_base_dir, brdf_dirs)
    three_tup = os.walk(dbDir)
    hdfList = []
    for (hdfHome, dirlist, filelist) in three_tup:
        for file in filelist: 
            if file.endswith(".hdf.gz") or file.endswith(".hdf"):
                hdfList.append(file)


    # Initialise the brdf dictionary to store the results
    brdf_dict = {}

    # Create a BRDF directory in the work path to store the intermediate
    # files such as format conversion and subsets.
    brdf_out_path = os.path.join(work_path, 'brdf_intermediates')
    if not os.path.exists(brdf_out_path):
        os.makedirs(brdf_out_path)

    # Loop over each defined band and each BRDF factor
    for band in brdf_lut.keys():
        bandwl = brdf_lut[band] # Band wavelength
        for factor in brdf_factors:
            hdfFileName = findFile(hdfList, bandwl, factor)
            assert hdfFileName is not None, 'Could not find HDF file for: %s, %s' % (bandwl, factor)

            hdfFile = os.path.join(hdfHome, hdfFileName)

            # Test if the file exists and has correct permissions
            try:
                with open(hdfFile, 'rb') as f:
                    pass
            except IOError as e:
                print "Unable to open file %s" % hdfFile


            # Unzip if we need to
            if hdfFile.endswith(".hdf.gz"):
                hdf_file = os.path.join(
                    work_path,
                    re.sub(".hdf.gz", ".hdf", 
                        os.path.basename(hdfFile)))
                gunzipCmd = "gunzip -c %s > %s" % (hdfFile, hdf_file)
                (status, msg) = commands.getstatusoutput(gunzipCmd)
                assert status == 0, "gunzip failed: %s" % msg
            else:
                hdf_file = hdfFile


            """
            The following now converts the file format and outputs a subset.
            This should proove useful for debugging and testing.
            """
            # Load the file
            brdf_object = BRDFLoader(hdf_file, UL=nw, LR=se)


            # setup the output filename
            out_fname = '_'.join(['Band', str(band), bandwl, factor])
            out_fname = os.path.join(brdf_out_path, out_fname)


            # Convert the file format
            brdf_object.convert_format(out_fname)


            # Read the subset and geotransform that corresponds to the subset
            subset, geot, prj = read_subset(out_fname, extents['UL'],
                extents['UR'], extents['LR'], extents['LL'])


            # The brdf_object has the scale and offsets so calculate the mean
            # through the brdf_object
            brdf_mean_value = brdf_object.get_mean(subset)


            # Output the brdf subset
            out_fname_subset = out_fname + '_subset'
            ut.write_img(subset, out_fname_subset, projection=prj,
                geotransform=geot)


            # Remove temporary unzipped file
            if hdf_file.find(work_path) == 0:
                os.remove(hdf_file)


            # Add the brdf filename and mean value to brdf_dict
            brdf_dict[(band, factor)] = {'data_source': 'BRDF',
                                         'data_file': hdfFile,
                                         'value': brdf_mean_value}

    return brdf_dict