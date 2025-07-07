import collections
import itertools
import os
import sys
import argparse
import json

import numpy
import pysam

from camparee.annotation_info import AnnotationInfo
from camparee.abstract_camparee_step import AbstractCampareeStep
from camparee.camparee_constants import CAMPAREE_CONSTANTS

class IntronQuantificationStep(AbstractCampareeStep):
    def __init__(self, log_directory_path, data_directory_path, parameters):
        #TODO: I dont thing the data directory or the log directory are ever
        #      used in the code below. Should we remove them? Or adapt the code
        #      to make use of them instead?
        self.log_directory_path = log_directory_path
        self.data_directory_path = data_directory_path
        self.info = None
        self.intron_normalized_antisense_counts = collections.Counter()
        self.intron_normalized_counts = collections.Counter()
        self.transcript_intron_antisense_counts = collections.Counter()
        self.transcript_intron_counts = collections.Counter()
        self.flank_size = parameters["flank_size"]
        self.intergenic_read_counts = collections.defaultdict(collections.Counter)
        self.forward_read_is_sense = parameters["forward_read_is_sense"]

    def validate(self):
        # TODO: do we need proper validation?
        return True

    def execute(self, aligned_file_path, output_directory, geneinfo_file_path):
        # (chrom, strand) -> {(transcriptID, intron_number) -> read_count}
        intron_read_counts = collections.Counter()
        intron_antisense_read_counts = collections.Counter()
        # chrom -> {intron -> read count}

        # Open BAM file with pysam
        # NOTE: use the check_sq=False flag since sometimes pysam complains erroneously about BAM headers
        # even though the header appears fine in samtools
        print(f"Opening alignment file {aligned_file_path}")
        with pysam.AlignmentFile(aligned_file_path, "rb") as alignments:

            chrom_lengths = dict(zip(alignments.references, alignments.lengths))

            #  Read in the annotation information
            self.info = AnnotationInfo(geneinfo_file_path, chrom_lengths)
            print(f"Read in annotation info file {geneinfo_file_path}")

            unpaired_reads = dict()

            # Go through all reads, and compare
            skipped_chromosomes = []
            for read in alignments.fetch(until_eof=True):
                # Use only uniquely mapped reads with both pairs mapped
                if read.is_unmapped or not read.is_proper_pair or not read.get_tag("NH") == 1:
                    continue

                try:
                    mate = unpaired_reads[read.query_name]
                except KeyError:
                    # mate not cached for processing, so cache this one
                    unpaired_reads[read.query_name] = read
                    continue

                # Read is paired to 'mate', so we process both together now
                # So remove the mate from the cache since we're done with it
                del unpaired_reads[read.query_name]

                chrom = read.reference_name
                # Check annotations are available for that chromosome
                if chrom not in self.info.intergenics:
                    if chrom not in skipped_chromosomes:
                        print(f"Alignment from chromosome {chrom} skipped")
                        skipped_chromosomes.append(chrom)
                    continue

                # According to the SAM file specification, this CAN fail but I don't understand why it would
                # so just throw this assert in to verify that it doesn't, at least for now
                assert read.is_reverse != mate.is_reverse

                # Figure out the fragment's strand - depends on whether the forward or reverse reads are 'sense'
                read1_reverse_aligned = (read.is_reverse and read.is_read1) or (not read.is_reverse and read.is_read2)
                if self.forward_read_is_sense:
                    strand = "-" if read1_reverse_aligned else "+"
                else:
                    strand = "+" if read1_reverse_aligned else "-"
                antisense_strand = "-" if strand == "+" else "+"

                # Use get() method to prevent KeyError if any of these data structures
                # are empty. This can occur for small chromosomes and non-standard
                # contigs.
                mintron_starts, mintron_ends = self.info.mintron_extents_by_chrom.get((chrom, strand), [None, None])
                antisense_mintron_starts, antisense_mintron_ends = self.info.mintron_extents_by_chrom.get((chrom, antisense_strand), [None, None])
                intergenic_starts, intergenic_ends = self.info.intergenic_extents_by_chrom.get(chrom, [None, None])

                mintrons_touched = set()
                antisense_mintrons_touched = set()
                intergenics_touched = set()

                # check what regions our blocks touch
                for start, end in itertools.chain(read.get_blocks(), mate.get_blocks()):
                    # NOTE: pysam is working in 0-based, half-open coordinates! We are using 1-based
                    start = start + 1

                    # If any mintrons were found in the current chromosome/strand
                    if mintron_starts is not None and mintron_ends is not None:
                        # We may intersect this mintron, depending on where its end lies
                        # or this could be -1 if we start before all mintrons
                        last_mintron_before = numpy.searchsorted(mintron_starts, start, side="right") - 1

                        # We definitely do not intersect this mintron, it starts after our end
                        first_mintron_after = numpy.searchsorted(mintron_starts, end, side="right")
                        # but all between the two, we do intersect

                        if last_mintron_before == -1:
                            # No introns start before us
                            mintrons_touched.update(range(0, first_mintron_after))
                        elif mintron_ends[last_mintron_before] >= start:
                            # We do intersect the last one to start before us
                            mintrons_touched.update(range(last_mintron_before, first_mintron_after))
                        else:
                            # We only start intersecting the first one after that
                            mintrons_touched.update(range(last_mintron_before + 1, first_mintron_after))

                    # If any antisense mintrons were found in the current chromosome/strand
                    if antisense_mintron_starts is not None and antisense_mintron_ends is not None:
                        # Now do the same thing as above for antisense regions
                        last_antisense_mintron_before = numpy.searchsorted(antisense_mintron_starts, start, side="right") - 1
                        first_antisense_mintron_after = numpy.searchsorted(antisense_mintron_starts, end, side="right")
                        if last_antisense_mintron_before == -1:
                            antisense_mintrons_touched.update(range(0, first_antisense_mintron_after))
                        elif antisense_mintron_ends[last_antisense_mintron_before] >= start:
                            antisense_mintrons_touched.update(range(last_antisense_mintron_before, first_antisense_mintron_after))
                        else:
                            antisense_mintrons_touched.update(range(last_antisense_mintron_before + 1, first_antisense_mintron_after))

                    # If any intergenic regions were found in the current chromosome
                    # (should only be an issue for small contigs, some prokaryotic
                    # genomes, and other less common cases)
                    if intergenic_starts is not None and intergenic_ends is not None:
                        # Now do the same thing as above for intergenic regions
                        last_intergenic_before = numpy.searchsorted(intergenic_starts, start, side="right") - 1
                        first_intergenic_after = numpy.searchsorted(intergenic_starts, end, side="right")
                        if last_intergenic_before == -1:
                            intergenics_touched.update(range(0, first_intergenic_after))
                        elif intergenic_ends[last_intergenic_before] >= start:
                            intergenics_touched.update(range(last_intergenic_before, first_intergenic_after))
                        else:
                            intergenics_touched.update(range(last_intergenic_before + 1, first_intergenic_after))

                # Accumulate the reads
                for mintron_index in mintrons_touched:
                    for intron in self.info.mintrons_by_chrom[chrom, strand][mintron_index].primary_introns:
                        intron_read_counts[intron] += 1

                for antisense_mintron_index in antisense_mintrons_touched:
                    for intron in self.info.mintrons_by_chrom[chrom, antisense_strand][antisense_mintron_index].primary_antisense_introns:
                        intron_antisense_read_counts[intron] += 1

                for intergenic in intergenics_touched:
                    self.intergenic_read_counts[chrom][intergenic] += 1


        # Normalize reads by effective transcript lengths
        for intron, count in intron_read_counts.items():
            effective_count = count / intron.effective_length * 1000 # FPK (fragments per kilo-base)
            self.intron_normalized_counts[intron] = effective_count
            # TODO: is this the right normalization factor for antisense reads?
            #  currently use the total length of all antisense mintrons, but this seems wrong....
            antisense_count = intron_antisense_read_counts[intron]
            if antisense_count > 0:
                self.intron_normalized_antisense_counts[intron] = antisense_count / intron.antisense_effective_length * 1000

        # Now remove counts from non-primary introns from mintrons, under the assumption that
        # if two introns overlap, we want to "subtract out" one of them from the overlap
        # We process introns in order of their transcript start: the idea being that we are modifying their
        # intron counts, so we had better be consistent as we process, going from 5' to 3' ends
        for contig, transcripts in self.info.transcripts_by_chrom.items():
            for transcript in transcripts:
                for intron in transcript.introns:
                    count = self.intron_normalized_counts.get(intron, 0)
                    if count == 0:
                        continue

                    for mintron in intron.mintrons:
                        if intron not in mintron.primary_introns:
                            for other_intron in mintron.primary_introns:
                                other_counts = self.intron_normalized_counts[other_intron]
                                # Remove the double-counts but never give negative expression
                                self.intron_normalized_counts[other_intron] = min(other_counts - count, 0)

                    # Same for anti-sense
                    antisense_count = self.intron_normalized_counts.get(intron, 0)
                    for antisense_mintron in intron.antisense_mintrons:
                        if intron not in antisense_mintron.primary_antisense_introns:
                            for other_intron in antisense_mintron.primary_antisense_introns:
                                other_counts = self.intron_normalized_antisense_counts[other_intron]
                                # Remove the double-counts but never give negative expression
                                self.intron_normalized_antisense_counts[other_intron] = min(other_counts - antisense_count, 0)

        # Transcript-level intron quantifications
        for transcript_id, transcript in self.info.transcripts.items():
            # flanks are first-and-last introns
            # But for now we do not use them to quantify the total transcript-level
            # intron counts since they are not really introns but are very long and so
            # throw off the intron length normalization
            non_flank_introns = transcript.introns[1:-1]
            sense_counts = 0
            antisense_counts = 0
            total_length = 0
            for intron in non_flank_introns:
                # We use normalized_counts here and then "unnormalize" them by effective length since we have
                # already performed the correction of the above section (removing non-primary intron counts)
                sense_counts  += self.intron_normalized_counts[intron] * intron.effective_length
                antisense_counts  += self.intron_normalized_antisense_counts[intron] * intron.effective_length
                total_length += intron.effective_length

            if total_length == 0:
                self.transcript_intron_counts[transcript_id] = 0
                self.transcript_intron_antisense_counts[transcript_id] = 0
            else:
                self.transcript_intron_counts[transcript_id] = sense_counts / total_length
                self.transcript_intron_antisense_counts[transcript_id] = antisense_counts / total_length

        # Write out the results to output file
        # SENSE INTRON OUTPUT
        output_file_path = os.path.join(output_directory, CAMPAREE_CONSTANTS.INTRON_OUTPUT_FILENAME)
        with open(output_file_path, "w") as output_file:
            output_file.write("#gene_id\ttranscript_id\tchr\tstrand\ttranscript_intron_reads_FPK\tintron_reads_FPK\n")
            # take transcripts from all chromosomes and combine them, sorting by gene id and then transcript id
            transcript_ids = sorted((transcript.gene.gene_id, id, transcript) for id, transcript in self.info.transcripts.items())
            for (gene_id, transcript_id, transcript) in transcript_ids:

                total_count = self.transcript_intron_counts[transcript_id]
                intron_counts = [str(self.intron_normalized_counts[intron]) for intron in transcript.introns]


                output_file.write('\t'.join([gene_id,
                                             transcript_id,
                                             transcript.chrom,
                                             transcript.strand,
                                             str(total_count),
                                             ','.join(intron_counts),
                                            ]) + '\n')

        # ANTISENSE INTRON OUTPUT
        output_file_path = os.path.join(output_directory, CAMPAREE_CONSTANTS.INTRON_OUTPUT_ANTISENSE_FILENAME)
        with open(output_file_path, "w") as output_file:
            output_file.write("#gene_id\ttranscript_id\tchr\tstrand\ttranscript_intron_reads_FPK\tintron_reads_FPK\n")
            # take transcripts from all chromosomes and combine them, sorting by gene id and then transcript id
            transcript_ids = sorted((transcript.gene.gene_id, id, transcript) for id, transcript in self.info.transcripts.items())
            for (gene_id, transcript_id, transcript) in transcript_ids:

                total_count = self.transcript_intron_antisense_counts[transcript_id]
                intron_counts = [str(self.intron_normalized_antisense_counts[intron]) for intron in transcript.introns]


                output_file.write('\t'.join([gene_id,
                                             transcript_id,
                                             transcript.chrom,
                                             transcript.strand,
                                             str(total_count),
                                             ','.join(intron_counts),
                                            ]) + '\n')

        # TODO: do we need to normalize intergenic regions?
        #   Not clear that just dividing by their length is right since usually you have just
        #   bits and pieces expressed throughout
        output_intergenic_file_path = os.path.join(output_directory, CAMPAREE_CONSTANTS.INTERGENIC_OUTPUT_FILENAME)
        with open(output_intergenic_file_path, "w") as output_file:
            output_file.write("#chromosome\tintergenic_region_number\tstart\tend\treads_FPK\n")
            # take transcripts from all chromosomes and combine them, sorting by gene id and then transcript id
            chroms_sorted = sorted(self.info.intergenics.keys())
            for chrom in chroms_sorted:
                intergenics = self.info.intergenics[chrom]
                for i, intergenic in enumerate(intergenics):
                    count = self.intergenic_read_counts[chrom][i]

                    output_file.write('\t'.join([chrom,
                                                 str(i),
                                                 str(intergenic.start),
                                                 str(intergenic.end),
                                                 str(count),
                                                ]) + '\n')

    def get_commandline_call(self, aligned_file_path, output_directory, geneinfo_file_path):
        """
        Prepare command to execute the IntronQuantification from the command line,
        given all of the arugments used to run the execute() method.

        Parameters
        ----------
        aligned_file_path : string
            Path to BAM file aligned to genome.
        output_directory : string
            Directory where the following output files will be saved:
            {CAMPAREE_CONSTANTS.INTRON_OUTPUT_FILENAME},
            {CAMPAREE_CONSTANTS.INTRON_OUTPUT_ANTISENSE_FILENAME},
            {CAMPAREE_CONSTANTS.INTERGENIC_OUTPUT_FILENAME}.
        geneinfo_file_path : string
            Geneinfo file in BED format with 1-based, inclusive coordinates.

        Returns
        -------
        string
            Command to execute on the command line. It will perform the same
            operations as a call to execute() with the same parameters.

        """

        #Retrieve path to the intron_quant.py script.
        intron_quant_path = os.path.realpath(__file__)
        #If the above command returns a string with a "pyc" extension, instead
        #of "py", strip off "c" so it points to this script.
        intron_quant_path = intron_quant_path.rstrip('c')

        intron_quant_params = {}
        intron_quant_params['forward_read_is_sense'] = self.forward_read_is_sense
        intron_quant_params['flank_size'] = self.flank_size

        command = (f"python {intron_quant_path}"
                   f" --log_directory_path {self.log_directory_path}"
                   f" --data_directory_path {self.data_directory_path}"
                   f" --bam_file {aligned_file_path}"
                   f" --output_directory {output_directory}"
                   f" --info_file {geneinfo_file_path}"
                   f" --parameters '{json.dumps(intron_quant_params)}'")

        return command

    def get_validation_attributes(self, aligned_file_path, output_directory, geneinfo_file_path):
        """
        Prepare attributes required by is_output_valid() function to validate
        output generated the IntronQuantification job.

        Parameters
        ----------
        aligned_file_path : string
            Path to BAM file aligned to genome. [Note: this parameter is
            captured just so get_validation_attributes() accepts the same
            arguments as get_commandline_call(). It is not used here.]
        output_directory : string
            Directory where the following output files are saved:
            {CAMPAREE_CONSTANTS.INTRON_OUTPUT_FILENAME},
            {CAMPAREE_CONSTANTS.INTRON_OUTPUT_ANTISENSE_FILENAME},
            {CAMPAREE_CONSTANTS.INTERGENIC_OUTPUT_FILENAME}.
        geneinfo_file_path : string
            Geneinfo file in BED format with 1-based, inclusive coordinates.
            [Note: this parameter is captured just so get_validation_attributes()
            accepts the same arguments as get_commandline_call(). It is not used
            here.]

        Returns
        -------
        dict
            A IntronQuantification job's output_directory.
        """

        validation_attributes = {"output_directory" : output_directory}
        return validation_attributes

    @staticmethod
    def main():
        """
        Entry point into script. Allows script to be executed/submitted via the
        command line.
        """

        parser = argparse.ArgumentParser(description='Command line wrapper around'
                                                     ' the intron quantifier')
        parser.add_argument("--log_directory_path", help="directory to output logging files to", default=None)
        parser.add_argument("--data_directory_path", help="data directory folder (unused)", default=None)
        parser.add_argument("-b", "--bam_file", help="BAM or SAM file of strand-specific genomic alignment")
        parser.add_argument("-i", "--info_file", help="Geneinfo file in BED format with 1-based, inclusive coordinates")
        parser.add_argument("-o", "--output_directory", help=f"Directory where to output files {CAMPAREE_CONSTANTS.INTRON_OUTPUT_FILENAME}, {CAMPAREE_CONSTANTS.INTRON_OUTPUT_ANTISENSE_FILENAME}, {CAMPAREE_CONSTANTS.INTERGENIC_OUTPUT_FILENAME}")
        parser.add_argument("--forward_read_is_sense", help="Set if forward read is sense. Default is False. Strand-specificity is assumed", action="store_const", const=True, default=False)
        parser.add_argument("--parameters", help="jsonified config parameters", default='{}')

        args = parser.parse_args()

        print("Starting Intron/Intergenic Quantification step")
        parameters = json.loads(args.parameters)
        intron_quant = IntronQuantificationStep(args.log_directory_path, args.data_directory_path, parameters=parameters)
        intron_quant.execute(args.bam_file, args.output_directory, args.info_file)

    @staticmethod
    def is_output_valid(validation_attributes):
        #TODO: Add more thorought checks? Also check logging when that's added
        #      to the rest of the script.

        valid_output = False

        output_directory = validation_attributes['output_directory']

        #Check for the existence of the 3 output files.
        if os.path.isfile(os.path.join(output_directory, CAMPAREE_CONSTANTS.INTRON_OUTPUT_FILENAME)) and \
           os.path.isfile(os.path.join(output_directory, CAMPAREE_CONSTANTS.INTRON_OUTPUT_ANTISENSE_FILENAME)) and \
           os.path.isfile(os.path.join(output_directory, CAMPAREE_CONSTANTS.INTERGENIC_OUTPUT_FILENAME)):
            valid_output = True

        return valid_output


if __name__ == '__main__':
    sys.exit(IntronQuantificationStep.main())

"""
Example:
python intron_quant.py -b "/Users/tgbrooks/BEERS2.0/data/baby_sample1.bam" -i "/Users/tgbrooks/BEERS2.0/data/baby_genome.mm9/annotations" -o .
"""
