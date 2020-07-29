# Copyright 2020 The TensorStore Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Tests for tensorstore.TensorStore."""

import pickle
import re
import tempfile

import pytest
import tensorstore as ts
import numpy as np

pytestmark = pytest.mark.asyncio


async def test_open_array_driver():
  t = await ts.open({
      "driver": "array",
      "array": [[1, 2, 3], [4, 5, 6]],
      "dtype": "int32",
  })
  assert t.domain == ts.IndexDomain(shape=[2, 3])
  assert t.dtype == ts.int32
  assert t.readable == True
  assert t.writable == True
  assert t.mode == "rw"
  a = np.array(t)
  assert a.dtype == np.int32
  np.testing.assert_equal(a, [[1, 2, 3], [4, 5, 6]])

  t[1, 1] = np.int32(7)
  np.testing.assert_equal(np.array(t), [[1, 2, 3], [4, 7, 6]])

  t[1, 1] = 8
  np.testing.assert_equal(np.array(t), [[1, 2, 3], [4, 8, 6]])

  assert (await t.read()).flags.carray
  assert (await t.read(order="C")).flags.carray
  assert (await t.read(order=None)).flags.carray
  assert (await t.read(order="F")).flags.fortran

  with pytest.raises(
      TypeError, match=re.escape("`order` must be specified as 'C' or 'F'")):
    await t.read(order="X")


async def test_array():
  t = ts.array(np.array([[1, 2, 3], [4, 5, 6]], dtype=np.int64))
  assert t.spec().to_json(include_defaults=False) == {
      "driver": "array",
      "array": [[1, 2, 3], [4, 5, 6]],
      "dtype": "int64",
      "transform": {
          "input_inclusive_min": [0, 0],
          "input_exclusive_max": [2, 3]
      },
  }

  assert t[0].spec().to_json(include_defaults=False) == {
      "driver": "array",
      "array": [1, 2, 3],
      "dtype": "int64",
      "transform": {
          "input_inclusive_min": [0],
          "input_exclusive_max": [3]
      },
  }


async def test_open_ustring_dtype():
  t = await ts.open({
      "driver": "array",
      "array": ["this", "is", "a", "string", "array"],
      "dtype": "ustring",
  })
  assert t.domain == ts.IndexDomain(shape=[5])
  assert t.dtype == ts.ustring
  a = await t.read()
  assert a.dtype == object
  np.testing.assert_equal(
      a, np.array(["this", "is", "a", "string", "array"], dtype=object))


async def test_cast():
  t = ts.array(np.array([0, 1, 2, 3], dtype=np.int64))
  t_string = t.astype(bytes)
  np.testing.assert_equal(await t_string.read(), [b"0", b"1", b"2", b"3"])
  t_bool = ts.cast(t, bool)
  np.testing.assert_equal(await t_bool.read(), [False, True, True, True])


async def test_local_n5():
  with tempfile.TemporaryDirectory() as dir_path:
    dataset = ts.open({
        "driver": "n5",
        "kvstore": {
            "driver": "file",
            "path": dir_path,
        },
        "metadata": {
            "compression": {
                "type": "gzip"
            },
            "dataType": "uint32",
            "dimensions": [1000, 20000],
            "blockSize": [10, 10],
        },
        "create": True,
        "delete_existing": True,
    }).result()
    dataset[80:82, 99:102] = [[1, 2, 3], [4, 5, 6]]
    np.testing.assert_equal([[1, 2, 3], [4, 5, 6], [0, 0, 0]],
                            dataset[80:83, 99:102].read().result())


async def test_open_error_message():
  with pytest.raises(ValueError,
                     match=".*Error parsing object member \"driver\": .*"):
    await ts.open({"invalid": "key"})

  with pytest.raises(ValueError,
                     match="Expected object, but received: 3"):
    await ts.open(3)


async def test_pickle():
  with tempfile.TemporaryDirectory() as dir_path:
    context = ts.Context({"cache_pool": { "total_bytes_limit": 1000000}})
    spec = {
      "driver": "n5",
      "kvstore": {
        "driver": "file",
        "path": dir_path,
      },
      "metadata": {
        "compression": {
          "type": "raw",
        },
        "dataType": "uint32",
        "dimensions": [100, 100],
        "blockSize": [10, 10],
      },
      "recheck_cached_data": False,
      "recheck_cached_metadata": False,
      "create": True,
      "open": True,
    }
    t1 = await ts.open(spec, context=context)
    t2 = await ts.open(spec, context=context)

    pickled = pickle.dumps([t1, t2])
    unpickled = pickle.loads(pickled)
    new_t1, new_t2 = unpickled

    assert new_t1[0, 0].read().result() == 0
    assert new_t2[0, 0].read().result() == 0
    new_t1[0, 0] = 42

    # Delete data
    await ts.open(spec, create=True, delete_existing=True)

    # new_t1 still sees old data in cache
    assert new_t1[0, 0].read().result() == 42

    # new_t2 shares cache with new_t1
    assert new_t2[0, 0].read().result() == 42
