"""Testing conversion with conversion saved on datalad"""
from glob import glob
import os
import os.path as op
import re

import pytest

from heudiconv.cli.run import main as runner
from heudiconv.external.pydicom import dcm
from heudiconv.utils import load_json
# testing utilities
from .utils import fetch_data, gen_heudiconv_args, TESTS_DATA_PATH

have_datalad = True
try:
    from datalad.support.exceptions import IncompleteResultsError
except ImportError:
    have_datalad = False


@pytest.mark.skipif(not have_datalad, reason="no datalad")
@pytest.mark.parametrize('subject', ['sid000143'])
@pytest.mark.parametrize('heuristic', ['reproin.py'])
@pytest.mark.parametrize('anon_cmd', [None, 'anonymize_script.py'])
def test_conversion(tmpdir, subject, heuristic, anon_cmd):
    tmpdir.chdir()
    try:
        datadir = fetch_data(tmpdir.strpath,
                             "dbic/QA",  # path from datalad database root
                             getpath=op.join('sourcedata', f'sub-{subject}'))
    except IncompleteResultsError as exc:
        pytest.skip("Failed to fetch test data: %s" % str(exc))
    outdir = tmpdir.mkdir('out').strpath

    args = gen_heudiconv_args(
        datadir, outdir, subject, heuristic, anon_cmd,
        template='sourcedata/sub-{subject}/*/*/*.tgz'
    )
    runner(args)  # run conversion

    # Get the possibly anonymized subject id and verify that it was
    # anonymized or not:
    subject_maybe_anon = glob(f'{outdir}/sub-*')
    assert len(subject_maybe_anon) == 1  # just one should be there
    subject_maybe_anon = op.basename(subject_maybe_anon[0])[4:]

    if anon_cmd:
        assert subject_maybe_anon != subject
    else:
        assert subject_maybe_anon == subject

    # verify functionals were converted
    outfiles = sorted([f[len(outdir):] for f in glob(f'{outdir}/sub-{subject_maybe_anon}/func/*')])
    assert outfiles
    datafiles = sorted([f[len(datadir):] for f in glob(f'{datadir}/sub-{subject}/ses-*/func/*')])
    # original data has ses- but because we are converting only func, and not
    # providing any session, we will not "match". Let's strip away the session
    datafiles = [re.sub(r'[/\\_]ses-[^/\\_]*', '', f) for f in datafiles]
    if not anon_cmd:
        assert outfiles == datafiles
    else:
        assert outfiles != datafiles  # sid was anonymized
        assert len(outfiles) == len(datafiles)  # but we have the same number of files

    # compare some json metadata
    json_ = '{}/task-rest_acq-24mm64sl1000tr32te600dyn_bold.json'.format
    orig, conv = (load_json(json_(datadir)),
                  load_json(json_(outdir)))
    keys = ['EchoTime', 'MagneticFieldStrength', 'Manufacturer', 'SliceTiming']
    for key in keys:
        assert orig[key] == conv[key]


@pytest.mark.skipif(not have_datalad, reason="no datalad")
def test_multiecho(tmpdir, subject='MEEPI', heuristic='bids_ME.py'):
    tmpdir.chdir()
    try:
        datadir = fetch_data(tmpdir.strpath, "dicoms/velasco/MEEPI")
    except IncompleteResultsError as exc:
        pytest.skip("Failed to fetch test data: %s" % str(exc))

    outdir = tmpdir.mkdir('out').strpath
    args = gen_heudiconv_args(datadir, outdir, subject, heuristic)
    runner(args)  # run conversion

    # check if we have echo functionals
    echoes = glob(op.join('out', 'sub-' + subject, 'func', '*echo*nii.gz'))
    assert len(echoes) == 3

    # check EchoTime of each functional
    # ET1 < ET2 < ET3
    prev_echo = 0
    for echo in sorted(echoes):
        _json = echo.replace('.nii.gz', '.json')
        assert _json
        echotime = load_json(_json).get('EchoTime', None)
        assert echotime > prev_echo
        prev_echo = echotime

    events = glob(op.join('out', 'sub-' + subject, 'func', '*events.tsv'))
    for event in events:
        assert 'echo-' not in event


@pytest.mark.parametrize('subject', ['merged'])
def test_grouping(tmpdir, subject):
    dicoms = [
        op.join(TESTS_DATA_PATH, fl) for fl in ['axasc35.dcm', 'phantom.dcm']
    ]
    # ensure DICOMs are different studies
    studyuids = {
        dcm.read_file(fl, stop_before_pixels=True).StudyInstanceUID for fl
        in dicoms
    }
    assert len(studyuids) == len(dicoms)
    # symlink to common location
    outdir = tmpdir.mkdir('out')
    datadir = tmpdir.mkdir(subject)
    for fl in dicoms:
        os.symlink(fl, (datadir / op.basename(fl)).strpath)

    template = op.join("{subject}/*.dcm")
    hargs = gen_heudiconv_args(
        tmpdir.strpath,
        outdir.strpath,
        subject,
        'convertall.py',
        template=template
    )

    with pytest.raises(AssertionError):
        runner(hargs)

    # group all found DICOMs under subject, despite conflicts
    hargs += ["-g", "all"]
    runner(hargs)
    assert len([fl for fl in outdir.visit(fil='run0*')]) == 4
    tsv = (outdir / 'participants.tsv')
    assert tsv.check()
    lines = tsv.open().readlines()
    assert len(lines) == 2
    assert lines[1].split('\t')[0] == 'sub-{}'.format(subject)
