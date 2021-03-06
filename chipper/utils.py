import gzip
import json
import os

try:
    import cPickle as pickle
except:
    import pickle


def load_gz_p(file_name):
    with gzip.open(file_name, 'rb') as f:
        data = f.read()
    try:
        return pickle.loads(data, encoding='utf-8')
    except:
        return pickle.loads(data)


def save_gzip_pickle(file_name, obj):
    with gzip.open(file_name, 'wb') as f:
        f.write(pickle.dumps(obj, protocol=-1))


def load_old(f_name):
    song_data = []
    with gzip.open(f_name, 'rb') as fin:
        for line in fin:
            json_line = json.loads(line, encoding='utf-8')
            song_data.append(json_line)
    return song_data


def get_basename(files, suffix):
    return [os.path.basename(i) for i in files if i.lower().endswith(suffix)]


def get_file_prefix(files):
    return [os.path.splitext(i)[0] for i in files]
