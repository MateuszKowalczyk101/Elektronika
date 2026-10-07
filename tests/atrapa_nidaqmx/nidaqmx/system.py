class _Chans:
    channel_names = ["Dev1/ctr0", "Dev1/ctr1"]


class _Device:
    name = "Dev1"
    product_type = "USB-6210"
    ci_physical_chans = _Chans()


class System:
    @staticmethod
    def local():
        class _Local:
            devices = [_Device()]
        return _Local()
