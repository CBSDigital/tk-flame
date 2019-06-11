# Copyright (c) 2017 Shotgun Software Inc.
#
# CONFIDENTIAL AND PROPRIETARY
#
# This work is provided "AS IS" and subject to the Shotgun Pipeline Toolkit
# Source Code License included in this distribution package. See LICENSE.
# By accessing, using, copying or modifying this work you indicate your
# agreement to the Shotgun Pipeline Toolkit Source Code License. All rights
# not expressly granted therein are reserved by Shotgun Software Inc.

import os
import re

import sgtk

HookBaseClass = sgtk.get_hook_baseclass()


class CreateVersionPlugin(HookBaseClass):
    """
    Plugin for creating generic publishes in Shotgun
    """

    def __init__(self, *args, **kwrds):
        super(CreateVersionPlugin, self).__init__(*args, **kwrds)

        self.publisher = self.parent
        self.engine = self.publisher.engine
        self.sg = self.engine.shotgun

    @property
    def icon(self):
        """
        Path to an png icon on disk
        """

        # look for icon one level up from this hook's folder in "icons" folder
        return os.path.join(
            self.disk_location,
            os.pardir,
            "icons",
            "publish.png"
        )

    @property
    def name(self):
        """
        One line display name describing the plugin
        """
        return "Create Version"

    @property
    def description(self):
        """
        Verbose, multi-line description of what the plugin does. This can
        contain simple html for formatting.
        """
        return "Creates version in Shotgun for the given object"

    @property
    def settings(self):
        """
        Dictionary defining the settings that this plugin expects to recieve
        through the settings parameter in the accept, validate, publish and
        finalize methods.

        A dictionary on the following form:

            {
                "Settings Name": {
                    "type": "settings_type",
                    "default": "default_value",
                    "description": "One line description of the setting"
            }

        The type string should be one of the data types that toolkit accepts
        as part of its environment configuration.
        """
        return {}

    @property
    def item_filters(self):
        """
        List of item types that this plugin is interested in.

        Only items matching entries in this list will be presented to the
        accept() method. Strings can contain glob patters such as *, for example
        ["maya.*", "file.maya"]
        """
        return ["flame.video", "flame.movie", "flame.openClip", "flame.batchOpenClip"]

    def accept(self, settings, item):
        """
        Method called by the publisher to determine if an item is of any
        interest to this plugin. Only items matching the filters defined via the
        item_filters property will be presented to this method.

        A publish task will be generated for each item accepted here. Returns a
        dictionary with the following booleans:

            - accepted: Indicates if the plugin is interested in this value at
                all. Required.
            - enabled: If True, the plugin will be enabled in the UI, otherwise
                it will be disabled. Optional, True by default.
            - visible: If True, the plugin will be visible in the UI, otherwise
                it will be hidden. Optional, True by default.
            - checked: If True, the plugin will be checked in the UI, otherwise
                it will be unchecked. Optional, True by default.

        :param settings: Dictionary of Settings. The keys are strings, matching
            the keys returned in the settings property. The values are `Setting`
            instances.
        :param item: Item to process

        :returns: dictionary with boolean keys accepted, required and enabled
        """

        return {"accepted": True}

    def validate(self, settings, item):
        """
        Validates the given item to check that it is ok to publish.

        Returns a boolean to indicate validity.

        :param settings: Dictionary of Settings. The keys are strings, matching
            the keys returned in the settings property. The values are `Setting`
            instances.
        :param item: Item to process

        :returns: True if item is valid, False otherwise.
        """

        return True

    def publish(self, settings, item):
        """
        Executes the publish logic for the given item and settings.

        :param settings: Dictionary of Settings. The keys are strings, matching
            the keys returned in the settings property. The values are `Setting`
            instances.
        :param item: Item to process
        """
        path = item.properties.get("path", None)

        # Build the Version metadata dictionary
        ver_data = dict(
            project=item.context.project,
            code=item.name,
            description=item.description,
            entity=item.context.entity,
            sg_task=item.context.task,
            sg_path_to_frames=path
        )

        ver_data["sg_department"] = "Flame"

        asset_info = item.properties.get("assetInfo", {})

        frame_rate = asset_info.get("fps")
        if frame_rate:
            ver_data["sg_uploaded_movie_frame_rate"] = float(frame_rate)

        aspect_ratio = asset_info.get("aspectRatio")
        if asset_info:
            ver_data["sg_frames_aspect_ratio"] = float(aspect_ratio)
            ver_data["sg_movie_aspect_ratio"] = float(aspect_ratio)

        # For file sequences, we want the path as provided by flame.
        # The property 'path' will be encoded the shotgun way file.%d.ext
        # while 'file_path' will be encoded the flame way file.[##-##].ext.
        file_path = item.properties.get("file_path", path)

        re_match = re.search(r"(\[[0-9]+-[0-9]+\])\.", file_path)
        if re_match:
            ver_data["frame_range"] = re_match.group(1)[1:-1]

        if "sourceIn" in asset_info and "sourceOut" in asset_info:
            ver_data["sg_first_frame"] = asset_info["sourceIn"]
            ver_data["sg_last_frame"] = asset_info["sourceOut"] - 1
            ver_data["frame_count"] = int(ver_data["sg_last_frame"]) - int(ver_data["sg_first_frame"]) + 1

        # Create the Version
        version = self.sg.create("Version", ver_data)

        # Keep the version reference for the other plugins
        item.properties["Version"] = version

        dependencies = item.properties.get("backgroundJobId")

        # CBSD Customization
        # ==================================
        # Create the Movie in background
        # (Thumbnail will be generated server-side from movie)
        #
        # Instead of using the normal preview generator for a low quality quicktime,
        # Create and Publish a deliverable quicktime based on Project Settings in Shotgun

        # @TODO Use a template to define the destination location of the quicktime
        quicktime_name = os.path.split(path)[1].split(".")[0] + ".mov"
        quicktime_path = os.path.join(os.path.dirname(path), quicktime_name)

        quicktime_job = self.generate_quicktime(
            display_name=quicktime_name,
            src_path=file_path,
            dst_path=quicktime_path,
            dependencies=dependencies,
            target_entities=[version],
            asset_info=asset_info
        )

        item.properties["quicktime_name"] = quicktime_name
        item.properties["quicktime_path"] = quicktime_path
        item.properties["quicktime_job"] = quicktime_job
        # ==================================

    # CBSD Customization
    # ==================================
    def _upload_quicktime_job(self, quicktime_job):
        """
        Create a Backburner job to upload a preview and link it to entities.

        Copied from `tk_flame.ThumbnailGeneratorFlame` and modified to call alternate hook
        to copy the quicktime movie from the `engine.get_backburner_tmp()` location to the
        Comp's renders folder.

        :param quicktime_job: Quicktime generation job information.
        :return: Backburner job ID created.
        """
        field_name = "sg_uploaded_movie"

        job_context = "Move and Upload Flame Quicktime"
        job_name = self.engine.sanitize_backburner_job_name(
            job_name=quicktime_job.get("display_name"),
            job_suffix=" - %s" % job_context
        )
        job_description = "%s for %s\n %s" % (
            job_context,
            quicktime_job.get("display_name"),
            quicktime_job.get("path")
        )

        return self.engine.create_local_backburner_job(
            job_name=job_name,
            description=job_description,
            dependencies=quicktime_job.get("dependencies"),
            instance="backburner_hooks",
            method_name="copy_from_temp_and_upload_to_shotgun",
            args={
                "targets": quicktime_job.get("target_entities"),
                "path": quicktime_job.get("path"),
                "field_name": field_name,
                "display_name": quicktime_job.get("display_name"),
                "files_to_delete": quicktime_job.get("files_to_delete")
            }
        )
    # ==================================

    # CBSD Customization
    # ==================================
    def finalize(self, settings, item):
        """
        Execute the finalization pass. This pass executes once
        all the publish tasks have completed, and can for example
        be used to version up files.

        :param settings: Dictionary of Settings. The keys are strings, matching
            the keys returned in the settings property. The values are `Setting`
            instances.
        :param item: Item to process
        """

        # A Given path can have both a thumbnail or a preview to upload since
        # not all entity type support a preview upload
        quicktime_job = item.properties.get("quicktime_job")
        if quicktime_job:
            return self._upload_quicktime_job(quicktime_job)
    # ==================================

    # CBSD Customization
    # ==================================
    def generate_quicktime(self, src_path, dst_path, display_name, target_entities, asset_info, dependencies):
        """
        Generate a local movie file from a Flame exported assets and link
        it to a list of Shotgun entities in the Path to movie field.

        :param src_path: Path to the media for which a local movie need to be
            generated and linked to Shotgun.
        :param dst_path: Path to local movie file to generate.
        :param display_name: The display name of the item we are generating the
            movie for. This will usually be the based name of the path.
        :param target_entities: Target entities to which the movie need to
            be linked to.
        :param asset_info: Dictionary of attribute passed by Flame's python
            hooks collected either thru an export (sg_export_hooks.py) or a
            batch render (sg_batch_hooks.py).
        :param dependencies: List of backburner job IDs this movie file
            generation job need to wait in order to be started. Can be None if
            the media is created in foreground.
        """
        (dst_path, job_id, files_to_delete) = self.engine.transcoder.transcode(
            src_path=src_path,
            dst_path=dst_path,
            extension=os.path.splitext(dst_path)[-1],
            display_name=display_name,
            job_context="Create Shotgun Local Movie",
            # @TODO get the preset to use from a field somewhere in Shotgun.
            preset_path=self.engine.local_movies_preset_path,
            asset_info=asset_info,
            dependencies=dependencies,
            poster_frame=None
        )

        self.engine.create_local_backburner_job(
            job_name="%s - Updating Shotgun Path to movie" % display_name,
            description="Uploading Shotgun Path to movie to %s" % dst_path,
            dependencies=job_id,
            instance="backburner_hooks",
            method_name="update_path_to_movie",
            args={
                "targets": target_entities,
                "path": dst_path,
                "files_to_delete": files_to_delete
            }
        )

        quicktime_job = {
            "display_name": display_name,
            "dependencies": job_id,
            "target_entities": target_entities,
            "path": dst_path,
            "files_to_delete": files_to_delete
        }

        return quicktime_job
    # ==================================