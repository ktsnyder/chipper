import os
import threading
import time

import numpy as np
import pandas as pd
from kivy.logger import Logger
from kivy.properties import StringProperty
from kivy.uix.screenmanager import Screen
from skimage.measure import label, regionprops

import chipper.utils as utils

Logger.disabled = False


class Analysis(Screen):
    user_note_thresh = StringProperty()
    user_syll_sim_thresh = StringProperty()

    # stop = threading.Event()  # will need if the thread for analysis is not daemon

    def __init__(self, *args, **kwargs):
        super(Analysis, self).__init__(*args, **kwargs)

    def log(self, output):
        Logger.info("analysis : {}".format(output))

    def thread_process(self):
        th = threading.Thread(target=self.analyze,
                              args=(self.parent.directory,
                                    self.parent.files,
                                    ))
        th.daemon = True
        th.start()

    def analyze(self, directory, files, out_path=None):
        if out_path is None:
            out_path = directory + "/AnalysisOutput_" + time.strftime(
                "%Y%m%d_T%H%M%S")
        file_names = [os.path.join(directory, i) for i in files]
        # file_names = [i for i in os.listdir(directory) if i.endswith('.gzip')]
        # file_names =
        # files = [os.path.join(directory, i) for i in file_names]
        assert len(files) != 0, "No gzipped files in {}".format(directory)

        final_output = []
        n_files = len(files)

        for i in range(n_files):
            self.ids.processing_count.text = \
                "{} of {} complete".format(i, n_files)

            final_output.append(Song(file_names[i], self.user_note_thresh,
                                     self.user_syll_sim_thresh).run_analysis())

        self.ids.processing_count.text = "{0} of {0} complete".format(n_files)

        output_bout_data(out_path, files, final_output)
        self.ids.analysis_layout.remove_widget(self.ids.progress_spinner)
        self.ids.analysis_done.disabled = False


class Song(object):
    def __init__(self, file_name, note_thresh, syll_sim_thresh, testing=False):
        self.file_name = file_name
        ons, offs, thresh, ms, htz = load_bout_data(self.file_name)
        self.onsets = ons
        self.offsets = offs
        self.threshold_sonogram = thresh
        self.ms_per_pixel = ms
        self.hertzPerPixel = htz
        self.syll_dur = self.offsets - self.onsets
        self.n_syll = len(self.syll_dur)
        self.note_thresh = int(note_thresh)
        self.syll_sim_thresh = float(syll_sim_thresh)
        self._test = testing

    def log(self, output):
        Logger.info("analysis : {}".format(output))

    def run_analysis(self):
        """ Runs entire analysis to describe song

        Returns
        -------
        Dictionary of dictionaries
        """

        # run analysis
        bout_stats = self.get_bout_stats()

        syllable_stats = self.get_syllable_stats()

        note_stats = self.get_note_stats()

        # write output
        final_output = update_dict([bout_stats, syllable_stats, note_stats])
        return final_output

    def get_note_stats(self):
        num_notes, props, _ = get_notes(
            threshold_sonogram=self.threshold_sonogram,
            onsets=self.onsets, offsets=self.offsets)
        # initialize, will be altered if the "note" is too small (<60 pixels)
        num_notes_updated = num_notes

        # stats per note
        notes_freq_range_upper = []
        notes_freq_range_lower = []
        note_length = []

        for j in range(num_notes):

            # use only the part of the matrix with the note
            sonogram_one_note = props[j].filled_image

            # check the note is large enough to be a note and not
            if np.size(sonogram_one_note) <= self.note_thresh:
                # just noise
                note_length.append(0)  # place holder
                num_notes_updated -= 1
                # print('not a note')
            else:
                # use bounding box of the note
                # (note, indexing is altered since python starts with 0 and
                # we want to convert rows to actual frequency)
                min_row, min_col, max_row, max_col = props[j].bbox
                # min row is inclusive (first row with labeled section)
                notes_freq_range_upper.append(min_row)
                # max row is not inclusive
                # (first zero row after the labeled section)
                notes_freq_range_lower.append(max_row)
                note_length.append(np.shape(sonogram_one_note)[1])

        # collect stats into dictionaries for output
        note_length_array = np.asarray(note_length)
        note_length_array = note_length_array[note_length_array != 0]
        note_length_array_scaled = note_length_array * self.ms_per_pixel
        note_counts = {'note_size_threshold': self.note_thresh,
                       'num_notes': num_notes_updated,
                       'num_notes_per_syll': num_notes_updated / self.n_syll}

        basic_note_stats = get_basic_stats(note_length_array_scaled,
                                           'note_duration', '(ms)')
        freq_stats = self.get_freq_stats(notes_freq_range_upper,
                                         notes_freq_range_lower, 'notes')

        note_stats = update_dict([note_counts, basic_note_stats, freq_stats])
        return note_stats

    def get_bout_stats(self):
        """ Analyze Bout

        use onsets and offsets to get basic bout information (algebraic calcs)
        """
        syllable_durations_scaled = self.syll_dur * self.ms_per_pixel

        silence_durations_scaled = [self.onsets[i] - self.offsets[i - 1]
                                    for i in range(1, len(self.onsets))]
        silence_durations_scaled *= self.ms_per_pixel

        bout_duration_scaled = (self.offsets[-1] - self.onsets[0])
        bout_duration_scaled *= self.ms_per_pixel

        num_syll_per_bout_duration = self.n_syll / bout_duration_scaled

        song_stats = {
            'bout_duration(ms)': bout_duration_scaled,
            'num_syllables': self.n_syll,
            'num_syllable_per_bout_duration(1/ms)': num_syll_per_bout_duration
        }
        basic_syllable_stats = get_basic_stats(syllable_durations_scaled,
                                               'syllable_duration', '(ms)')
        basic_silence_stats = get_basic_stats(silence_durations_scaled,
                                              'silence_duration', '(ms)')
        bout_stats = update_dict(
            [song_stats, basic_syllable_stats, basic_silence_stats])

        return bout_stats

    def get_syllable_stats(self):

        # get syllable correlations for entire sonogram
        son_corr, son_corr_bin = get_sonogram_correlation(
            sonogram=self.threshold_sonogram, onsets=self.onsets,
            offsets=self.offsets, syll_duration=self.syll_dur,
            corr_thresh=self.syll_sim_thresh
        )

        if self._test:
            son_corr_2, son_corr_bin_2 = get_sonogram_correlation_old(
                sonogram=self.threshold_sonogram, onsets=self.onsets,
                offsets=self.offsets, syll_duration=self.syll_dur,
                corr_thresh=self.syll_sim_thresh
            )
            self.log('analysis: Method before\n{}'.format(son_corr_2))
            self.log('analysis: Method after\n{}'.format(son_corr))
            self.log('analysis: Are the same? {}'.format(
                np.isclose(son_corr, son_corr_2).all())
            )
            quit()
        # get syllable pattern
        syll_pattern = find_syllable_pattern(son_corr_bin)

        # find unique syllables
        n_unique_syll = len(np.unique(syll_pattern))
        num_syllables_per_num_unique = self.n_syll / n_unique_syll
        sequential_rep1 = 'NA'
        syll_stereotypy_final = 'NA'
        mean_syll_stereotypy = 'NA'
        std_syll_stereotypy = 'NA'

        if self.n_syll > 1:
            # determine how often the next syllable is the same as the
            # previous syllable (for chippies, should be oneless than number
            # of syllables in the bout)
            sequential_rep1 = len(
                np.where(np.diff(syll_pattern) == 0)[0]) / \
                              (len(syll_pattern) - 1)

            # determine syllable stereotypy
            syll_stereotypy, __, __ = calc_syllable_stereotypy(son_corr,
                                                               syll_pattern)
            mean_syll_stereotypy = np.nanmean(syll_stereotypy)
            std_syll_stereotypy = np.nanstd(syll_stereotypy, ddof=1)
            syll_stereotypy_final = syll_stereotypy[~np.isnan(syll_stereotypy)]

        syllable_stats_general = {
            'syll_correlation_threshold': self.syll_sim_thresh,
            'num_unique_syllables': n_unique_syll,
            'num_syllables_per_num_unique': num_syllables_per_num_unique,
            'syllable_pattern': syll_pattern.tolist(),
            'sequential_repetition': sequential_rep1,
            'syllable_stereotypy': syll_stereotypy_final,
            'mean_syllable_stereotypy': mean_syll_stereotypy,
            'std_syllable_stereotypy': std_syll_stereotypy}

        # syllable freq range
        freq_upper, freq_lower = calc_sylls_freq_ranges(
            self.offsets, self.onsets, self.threshold_sonogram
        )
        freq_stats = self.get_freq_stats(freq_upper, freq_lower, 'sylls')

        syllable_stats = update_dict([syllable_stats_general, freq_stats])

        return syllable_stats

    def get_freq_stats(self, freq_range_upper, freq_range_lower, data_type):
        rows = np.shape(self.threshold_sonogram)[0]

        avg_upper_freq = np.mean(freq_range_upper)
        # must subtract 1 since freq_range_lower is exclusive
        avg_lower_freq = np.mean(freq_range_lower) - 1
        # note: max freq is min row; min freq is max row -->
        # python matrix indexes starting at 0 (highest freq)
        max_freq = min(freq_range_upper)
        # must subtract 1 since freq_range_lower is exclusive
        min_freq = max(freq_range_lower) - 1
        # add one back so difference is accurate (need min_freq exclusive)
        overall_freq_range = abs(max_freq - min_freq) + 1

        avg_upper_freq_scaled = (rows - avg_upper_freq) * self.hertzPerPixel
        avg_lower_freq_scaled = (rows - avg_lower_freq) * self.hertzPerPixel
        max_freq_scaled = (rows - max_freq) * self.hertzPerPixel
        min_freq_scaled = (rows - min_freq) * self.hertzPerPixel
        overall_freq_range_scaled = overall_freq_range * self.hertzPerPixel

        freq_stats = {
            'avg_' + data_type + '_upper_freq(Hz)': avg_upper_freq_scaled,
            'avg_' + data_type + '_lower_freq(Hz)': avg_lower_freq_scaled,
            'max_' + data_type + '_freq(Hz)': max_freq_scaled,
            'min_' + data_type + '_freq(Hz)': min_freq_scaled,
            'overall_' + data_type + '_freq_range(Hz)': overall_freq_range_scaled
        }

        freq_modulation_per_note = abs(
            np.asarray(freq_range_upper) - np.asarray(
                freq_range_lower)) * self.hertzPerPixel
        basic_freq_stats = get_basic_stats(freq_modulation_per_note,
                                           data_type + '_freq_modulation',
                                           '(Hz)')

        freq_stats = update_dict([freq_stats, basic_freq_stats])

        return freq_stats


def calc_syllable_stereotypy(sonogram_corr, syllable_pattern_checked):
    n_corr = len(sonogram_corr)
    Logger.debug("analysis: length of sonogram {}".format(n_corr))

    syll_stereotypy = np.zeros(n_corr)
    syll_stereotypy_max = np.zeros(n_corr)
    syll_stereotypy_min = np.zeros(n_corr)
    Logger.debug("analysis: {}".format(n_corr))
    len_patt = len(syllable_pattern_checked)
    Logger.debug("analysis: pattern length {}".format(len_patt))
    for j in range(n_corr):
        # locations of all like syllables
        x_syll_locations = np.where(syllable_pattern_checked == j)[0]
        Logger.debug("analysis: x_syll locations\n{}".format(x_syll_locations))
        # initialize arrays
        x_syll_corr = np.zeros((len_patt, len_patt))
        if len(x_syll_locations) > 1:
            for k in range(len(x_syll_locations)):
                for h in range(len(x_syll_locations)):
                    # fill only the lower triangle (not upper or diagonal)
                    # so that similarities aren't double counted when
                    # taking the mean later
                    if k > h:
                        x_syll_corr[k, h] = sonogram_corr[x_syll_locations[k],
                                                          x_syll_locations[h]]
            Logger.debug(
                "analysis: x_syll_correlations\n{}".format(x_syll_corr))
            syll_stereotypy[j] = np.nanmean(x_syll_corr[x_syll_corr != 0])
            syll_stereotypy_max[j] = np.nanmax(x_syll_corr[x_syll_corr != 0])
            syll_stereotypy_min[j] = np.nanmin(x_syll_corr[x_syll_corr != 0])
        else:
            syll_stereotypy[j] = np.nan
            syll_stereotypy_max[j] = np.nan
            syll_stereotypy_min[j] = np.nan

    return syll_stereotypy, syll_stereotypy_max, syll_stereotypy_min


def get_sonogram_correlation(sonogram, onsets, offsets, syll_duration,
                             corr_thresh=50.0):
    n_offset = len(offsets)
    assert len(onsets) == n_offset, \
        "The number of offsets do not match the number of onsets"
    sonogram_correlation = np.zeros((n_offset, n_offset))

    mask = sonogram[:, :] < 1
    non_zero = np.where(~mask.all(1))
    min_y, max_y = np.min(non_zero), np.max(non_zero)
    sonogram = sonogram[min_y:max_y + 1, :]

    sonogram_self_correlation = calc_max_correlation(
        onsets, offsets, sonogram
    )
    for j in range(n_offset):
        sonogram_correlation[j, j] = 100
        ymin_1, ymax_1 = get_square(sonogram, onsets[j], offsets[j])
        # do not want to fill the second half of the diagonal matrix
        for k in range(j + 1, n_offset):
            ymin_2, ymax_2 = get_square(sonogram, onsets[k], offsets[k])

            if ymin_2 >= ymax_1 or ymin_1 >= ymax_2:
                sonogram_correlation[j, k] = 0
                sonogram_correlation[k, j] = 0
                continue

            y_min = min(ymin_1, ymin_2)
            # must add one due to python indexing
            y_max = max(ymax_1, ymax_2) + 1

            max_overlap = max(sonogram_self_correlation[j],
                              sonogram_self_correlation[k])

            s1_0 = sonogram[y_min:y_max, onsets[j]:offsets[j]]
            s2_0 = sonogram[y_min:y_max, onsets[k]:offsets[k]]
            # fill both upper and lower diagonal of symmetric matrix
            sonogram_correlation[j, k] = calc_corr(s1_0, s2_0, max_overlap)
            sonogram_correlation[k, j] = sonogram_correlation[j, k]

    sonogram_correlation_binary = np.zeros(sonogram_correlation.shape)
    sonogram_correlation_binary[sonogram_correlation >= corr_thresh] = 1

    return sonogram_correlation, sonogram_correlation_binary


def get_sonogram_correlation_old(sonogram, onsets, offsets, syll_duration,
                                 corr_thresh=50.0):
    sonogram_self_correlation = calc_max_correlation(
        onsets, offsets, sonogram
    )

    n_offset = len(offsets)
    sonogram_correlation = np.zeros((n_offset, n_offset))

    for j in range(n_offset):
        sonogram_correlation[j, j] = 100
        # do not want to fill the second half of the diagonal matrix
        for k in range(j + 1, n_offset):
            max_overlap = max(sonogram_self_correlation[j],
                              sonogram_self_correlation[k])

            shift_factor = abs(syll_duration[j] - syll_duration[k])

            if syll_duration[j] < syll_duration[k]:
                min_length = syll_duration[j]
                syll_corr = calc_corr_old(sonogram, onsets, j, k, shift_factor,
                                          min_length, max_overlap)

            # will be if k is shorter than j or they are equal
            else:
                min_length = syll_duration[k]
                syll_corr = calc_corr_old(sonogram, onsets, k, j, shift_factor,
                                          min_length, max_overlap)
            # fill both upper and lower diagonal of symmetric matrix
            sonogram_correlation[j, k] = syll_corr
            sonogram_correlation[k, j] = syll_corr

    sonogram_correlation_binary = np.zeros(sonogram_correlation.shape)
    sonogram_correlation_binary[sonogram_correlation >= corr_thresh] = 1
    return sonogram_correlation, sonogram_correlation_binary


def get_square(image, on, off):
    subset_1 = image[:, on:off]
    mask = subset_1[:, :] < 1
    non_zero = np.where(~mask.all(1))
    min_y, max_y = np.min(non_zero), np.max(non_zero)
    return min_y, max_y


def calc_corr(s1, s2, max_overlap):
    size_diff = s1.shape[1] - s2.shape[1]
    min_size = min(s1.shape[1], s2.shape[1])
    if size_diff < 0:
        s1, s2 = s2, s1
        size_diff *= -1
    syll_correlation = np.zeros(size_diff + 1)
    s2 = s2.flatten()
    for i in range(size_diff + 1):
        syll_correlation[i] = np.dot(s1[:, i:i + min_size].flatten(), s2).sum()
    return syll_correlation.max() * 100. / max_overlap


def calc_corr_old(sonogram, onsets, a, b, shift_factor, min_length,
                  max_overlap):
    syllable_correlation = np.zeros(shift_factor + 1)
    scale_factor = 100. / max_overlap
    # flatten matrix to speed up computations
    syll_1 = sonogram[:, onsets[a]:(onsets[a] + min_length)].flatten()
    for m in range(shift_factor + 1):
        start = onsets[b] + m
        # flatten matrix to speed up computations
        syll_2 = sonogram[:, start:start + min_length].flatten()
        syllable_correlation[m] = np.dot(syll_1, syll_2).sum()

    return syllable_correlation.max() * scale_factor


def get_notes(threshold_sonogram, onsets, offsets):
    """
    num of notes and categorization; also outputs freq ranges of each note
    """
    # zero anything before first onset or after last offset
    # (not offset row is already zeros, so okay to include)
    # this will take care of any noise before or after the song
    # before labeling the notes
    threshold_sonogram_crop = threshold_sonogram.copy()
    threshold_sonogram_crop[:, 0:onsets[0]] = 0
    threshold_sonogram_crop[:, offsets[-1]:-1] = 0

    # ^connectivity 1=4 or 2=8(include diagonals)
    labeled_sonogram, num_notes = label(threshold_sonogram_crop,
                                        return_num=True,
                                        connectivity=1)

    props = regionprops(labeled_sonogram)

    return num_notes, props, labeled_sonogram


def calc_max_correlation(onsets, offsets, sonogram):
    sonogram_self_correlation = np.zeros(len(onsets))

    for ind, (start, stop) in enumerate(zip(onsets, offsets)):
        sonogram_self_correlation[ind] = (sonogram[:, start:stop] *
                                          sonogram[:, start:stop]).sum()

    return sonogram_self_correlation


def calc_sylls_freq_ranges(offsets, onsets, sonogram):
    """ find unique syllables, syllable pattern, and stereotypy

    """
    sylls_freq_range_upper = []
    sylls_freq_range_lower = []
    for j in range(len(onsets)):
        start = onsets[j]
        stop = offsets[j]
        rows_with_signal = np.nonzero(np.sum(sonogram[:, start:stop], axis=1))[
            0]
        # will be inclusive
        sylls_freq_range_upper.append(rows_with_signal[0])
        # add one so that the lower will be exclusive
        sylls_freq_range_lower.append(rows_with_signal[-1] + 1)

    return sylls_freq_range_upper, sylls_freq_range_lower


def output_bout_data(output_path, file_name, output_dict):
    df_output = pd.DataFrame.from_dict(output_dict)
    df_output.index = [file_name]
    if not output_path.endswith('txt'):
        save_name = output_path + '.txt'
    else:
        save_name = output_path
    if not os.path.isfile(save_name) and not os.path.isfile(output_path):
        df_output.to_csv(save_name, sep="\t", index_label='FileName')
    else:
        df_output.to_csv(save_name, sep="\t", mode='a', header=False)


def load_bout_data(f_name):
    """
    Load sonogram and syllable marks (onsets and offsets).
    """
    try:
        song_data = utils.load_gz_p(f_name)
    except:
        song_data = utils.load_old(f_name)
    onsets = np.asarray(song_data[1]['Onsets'], dtype='int')
    offsets = np.asarray(song_data[1]['Offsets'], dtype='int')
    threshold_sonogram = np.asarray(song_data[2]['Sonogram'])
    ms_per_pixel = np.asarray(song_data[3]['timeAxisConversion'])
    hz_per_pixel = np.asarray(song_data[3]['freqAxisConversion'])
    return onsets, offsets, threshold_sonogram, ms_per_pixel, hz_per_pixel


def get_basic_stats(durations, data_type, units):
    # just in case there is one syllable and so silence_durations is empty
    if len(durations) == 0:
        stats = {'largest_' + data_type + units: 'NA',
                 'smallest_' + data_type + units: 'NA',
                 'avg_' + data_type + units: 'NA',
                 'std_' + data_type + units: 'NA'}
    else:
        stats = {'largest_' + data_type + units: max(durations),
                 'smallest_' + data_type + units: min(durations),
                 'avg_' + data_type + units: np.mean(durations),
                 'std_' + data_type + units: np.std(durations, ddof=1)}
    return stats


def update_dict(dictionaries):
    new_dict = {}
    for d in dictionaries:
        new_dict.update(d)
    return new_dict


def find_syllable_pattern(sonogram_correlation_binary):
    # get syllable pattern
    syllable_pattern = np.zeros(len(sonogram_correlation_binary), 'int')
    for j in range(len(sonogram_correlation_binary)):
        syllable_pattern[j] = np.nonzero(sonogram_correlation_binary[:, j])[0][
            0]

    # check syllable pattern -->
    # should be no new number that is smaller than it's index
    # (ex: 12333634 --> the 4 should be a 3 but didn't match up enough;
    # know this since 4 < pos(4) = 8)

    syllable_pattern_checked = np.zeros(syllable_pattern.shape, 'int')
    for j in range(len(syllable_pattern)):
        if syllable_pattern[j] < j:
            syllable_pattern_checked[j] = syllable_pattern[syllable_pattern[j]]
        else:
            syllable_pattern_checked[j] = syllable_pattern[j]

    return syllable_pattern_checked

